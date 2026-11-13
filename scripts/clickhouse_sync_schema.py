#!/usr/bin/env python3
"""Synchronize ClickHouse tables and views from db/clickhouse/*.xml.

Install the HTTP driver with ``pip install -r scripts/requirements.txt``. Set CLUSTER to
``true`` or ``false`` to select the matching engine and cluster DDL behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "db" / "clickhouse"


def env_bool(name: str, fallback: str | None = None, default: bool | None = None) -> bool:
    """Read a strict true/false environment variable."""
    value = os.environ.get(name)
    if value is None and fallback:
        value = os.environ.get(fallback)
    if value is None:
        if default is None:
            raise ValueError(f"Set {name}=true or {name}=false")
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def qident(value: str) -> str:
    """Quote a ClickHouse identifier."""
    return "`" + value.replace("`", "``") + "`"


def quote_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def expression(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return node.get("expression") or (node.text or "").strip()


def selected_engine(table: ET.Element, cluster: bool) -> str:
    variant = table.find("./engine/Cluster" if cluster else "./engine/UnCluster")
    if variant is None or not variant.get("type"):
        label = "Cluster" if cluster else "UnCluster"
        raise ValueError(f"table {table.get('name')!r} is missing engine/{label}")
    return variant.attrib["type"]


def column_definition(column: ET.Element) -> str:
    name, column_type = column.get("name"), column.get("type")
    if not name or not column_type:
        raise ValueError("column is missing name or type")
    item = f"{qident(name)} {column_type}"
    default = column.get("default")
    if default is not None:
        item += f" {column.get('default-kind', 'DEFAULT')} {default}"
    codec = column.get("codec")
    if codec:
        item += f" CODEC{codec if codec.startswith('(') else f'({codec})'}"
    if column.get("comment"):
        item += f" COMMENT {quote_literal(column.attrib['comment'])}"
    return item


def index_definition(index: ET.Element, if_not_exists: bool = False) -> str:
    required = ("name", "expression", "type", "granularity")
    missing = [key for key in required if not index.get(key)]
    if missing:
        raise ValueError(f"index is missing required attributes: {', '.join(missing)}")
    ine = " IF NOT EXISTS" if if_not_exists else ""
    return (
        f"INDEX{ine} {qident(index.attrib['name'])} {index.attrib['expression']} "
        f"TYPE {index.attrib['type']} GRANULARITY {index.attrib['granularity']}"
    )


def table_sql(table: ET.Element, cluster: bool, cluster_name: str) -> str:
    name = table.get("name")
    if not name:
        raise ValueError("table is missing its name")
    pieces = [column_definition(column) for column in table.findall("./columns/column")]
    pieces.extend(index_definition(index) for index in table.findall("./indexes/index"))
    if not pieces:
        raise ValueError(f"table {name!r} has no columns or indexes")
    cluster_clause = f" ON CLUSTER {qident(cluster_name)}" if cluster else ""
    ine = " IF NOT EXISTS" if table.get("if-not-exists", "true").lower() == "true" else ""
    sql = f"CREATE TABLE{ine} {qident(name)}{cluster_clause} (\n  " + ",\n  ".join(pieces) + "\n)"
    sql += f" ENGINE = {selected_engine(table, cluster)}"
    for clause in ("partition-by", "primary-key", "order-by", "sample-by", "ttl", "settings"):
        value = expression(table.find(clause))
        if value:
            sql += f" {clause.upper().replace('-', ' ')} {value}"
    return sql


def view_sql(view: ET.Element, cluster: bool, cluster_name: str) -> str | None:
    name, kind = view.get("name"), view.get("kind")
    query = (view.findtext("query") or "").strip().rstrip(";").strip()
    if not name or kind not in {"VIEW", "MATERIALIZED"} or not query:
        raise ValueError("each view requires name, kind (VIEW/MATERIALIZED), and query")
    if cluster != bool(view.get("on-cluster")):
        return None
    cluster_clause = f" ON CLUSTER {qident(cluster_name)}" if cluster else ""
    target = f" TO {qident(view.attrib['to'])}" if view.get("to") else ""
    if kind == "MATERIALIZED":
        return f"CREATE MATERIALIZED VIEW IF NOT EXISTS {qident(name)}{cluster_clause}{target} AS\n{query}"
    return f"CREATE OR REPLACE VIEW {qident(name)}{cluster_clause} AS\n{query}"


def load_schema(directory: Path) -> tuple[ET.Element, list[ET.Element], list[ET.Element]]:
    """Load tables and views from XML files in numeric filename order."""
    files = sorted(directory.glob("*.xml"))
    if not files:
        raise ValueError(f"No ClickHouse schema XML files found in {directory}")

    root = None
    tables, views = [], []
    for xml_file in files:
        current = ET.parse(xml_file).getroot()
        if current.tag != "database":
            raise ValueError(f"{xml_file}: root element must be <database>")
        if current.get("dialect", "clickhouse") != "clickhouse":
            raise ValueError(f"{xml_file}: only ClickHouse schemas are supported")
        if root is None:
            root = current
        tables.extend(current.findall("./tables/table"))
        views.extend(current.findall("./views/view"))
    return root, tables, views


def make_client():
    """Create the official ClickHouse HTTP client from application settings."""
    url = os.environ.get("CLICKHOUSE_URL")
    if not url:
        raise ValueError("CLICKHOUSE_URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("CLICKHOUSE_URL must be an http:// or https:// URL")
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise SystemExit("Install the ClickHouse driver with: pip install -r requirements.txt") from exc
    return clickhouse_connect.get_client(
        host=parsed.hostname,
        port=parsed.port or (8443 if parsed.scheme == "https" else 8123),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        database=os.environ.get("CLICKHOUSE_DB", "default"),
        secure=parsed.scheme == "https",
    )


def cluster_suffix(cluster: bool, cluster_name: str) -> str:
    return f" ON CLUSTER {qident(cluster_name)}" if cluster else ""


def ensure_ledger(client, cluster: bool, cluster_name: str) -> None:
    client.command(
        "CREATE TABLE IF NOT EXISTS `schema_xml_migration`"
        + cluster_suffix(cluster, cluster_name)
        + " (`statement_id` String, `checksum` String, `updated_at` DateTime64(3) DEFAULT now64(3))"
        " ENGINE = ReplacingMergeTree(updated_at) ORDER BY statement_id"
    )


def applied_checksums(client) -> dict[str, str]:
    rows = client.query(
        "SELECT statement_id, checksum FROM schema_xml_migration FINAL"
    ).result_rows
    return {ident: checksum for ident, checksum in rows}


def record_applied(client, ident: str, checksum: str) -> None:
    client.insert(
        "schema_xml_migration", [[ident, checksum]],
        column_names=["statement_id", "checksum"],
    )


def current_table_engine(client, database: str, table: str) -> str | None:
    rows = client.query(
        "SELECT engine FROM system.tables WHERE database = {database:String} AND name = {table:String}",
        parameters={"database": database, "table": table},
    ).result_rows
    return rows[0][0] if rows else None


def current_columns(client, database: str, table: str) -> dict[str, str]:
    rows = client.query(
        "SELECT name, type FROM system.columns WHERE database = {database:String} AND table = {table:String}",
        parameters={"database": database, "table": table},
    ).result_rows
    return {name: column_type for name, column_type in rows}


def current_indexes(client, database: str, table: str) -> dict[str, tuple[str, str, int]]:
    rows = client.query(
        "SELECT name, expr, type_full, granularity FROM system.data_skipping_indices "
        "WHERE database = {database:String} AND table = {table:String}",
        parameters={"database": database, "table": table},
    ).result_rows
    return {name: (expr, index_type, granularity) for name, expr, index_type, granularity in rows}


def sync_table(client, table: ET.Element, cluster: bool, cluster_name: str,
               database: str, applied: dict[str, str]) -> None:
    name = table.attrib["name"]
    existing_engine = current_table_engine(client, database, name)
    existed = existing_engine is not None
    expected_engine = selected_engine(table, cluster).split("(", 1)[0]
    if existing_engine and existing_engine != expected_engine:
        raise RuntimeError(
            f"table {name!r} uses engine {existing_engine}, but this schema selects "
            f"{expected_engine} (CLUSTER={str(cluster).lower()}); engine conversion requires a data migration"
        )
    client.command(table_sql(table, cluster, cluster_name))
    alter_prefix = f"ALTER TABLE {qident(name)}{cluster_suffix(cluster, cluster_name)} "

    columns = current_columns(client, database, name)
    for column in table.findall("./columns/column"):
        column_name, expected_type = column.attrib["name"], column.attrib["type"]
        if column_name not in columns:
            client.command(f"{alter_prefix}ADD COLUMN IF NOT EXISTS {column_definition(column)}")
        elif columns[column_name] != expected_type:
            client.command(f"{alter_prefix}MODIFY COLUMN {column_definition(column)}")

    indexes_node = table.find("./indexes")
    expected_indexes = {
        index.attrib["name"]: index for index in table.findall("./indexes/index")
    }
    actual_indexes = current_indexes(client, database, name)
    for index_name, actual in actual_indexes.items():
        expected = expected_indexes.get(index_name)
        if expected is None and indexes_node is not None and indexes_node.get("sync", "false").lower() == "true":
            client.command(f"{alter_prefix}DROP INDEX IF EXISTS {qident(index_name)}")
            continue
        if expected is not None:
            signature = (
                expected.attrib["expression"], expected.attrib["type"],
                int(expected.attrib["granularity"]),
            )
            if actual != signature:
                client.command(f"{alter_prefix}DROP INDEX IF EXISTS {qident(index_name)}")

    actual_indexes = current_indexes(client, database, name)
    for index_name, index in expected_indexes.items():
        expected_signature = (
            index.attrib["expression"], index.attrib["type"], int(index.attrib["granularity"])
        )
        if actual_indexes.get(index_name) != expected_signature:
            client.command(f"{alter_prefix}ADD {index_definition(index, if_not_exists=True)}")

        if index.get("materialize", "false").lower() == "true":
            materialize_sql = f"{alter_prefix}MATERIALIZE INDEX IF EXISTS {qident(index_name)}"
            materialize_state = (
                f"{materialize_sql}\n{index.attrib['expression']}\n{index.attrib['type']}\n"
                f"{index.attrib['granularity']}"
            )
            checksum = hashlib.sha256(materialize_state.encode("utf-8")).hexdigest()
            ledger_id = f"index-{name}-{index_name}-materialize"
            if applied.get(ledger_id) != checksum:
                if existed:
                    client.command(materialize_sql)
                record_applied(client, ledger_id, checksum)
                applied[ledger_id] = checksum


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize ClickHouse from XML schemas")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="directory containing database XML files")
    parser.add_argument("--dry-run", action="store_true", help="print planned SQL without connecting")
    parser.add_argument("--sql-out", type=Path, help="write the table and view DDL plan to a file")
    args = parser.parse_args()

    cluster = env_bool("CLUSTER", fallback="CLICKHOUSE_CLUSTER_ENABLED", default=False)
    cluster_name = os.environ.get("CLICKHOUSE_CLUSTER_NAME", "default")
    root, tables, views = load_schema(args.dir)
    table_plan = [(f"table-{table.attrib['name']}", table_sql(table, cluster, cluster_name)) for table in tables]
    view_plan = []
    for view in views:
        sql = view_sql(view, cluster, cluster_name)
        if sql:
            suffix = "cluster" if cluster else "uncluster"
            view_plan.append((f"view-{view.attrib['name']}-{suffix}", sql))

    planned = table_plan + view_plan
    generated = "\n\n".join(f"-- {ident}\n{sql};" for ident, sql in planned) + ("\n" if planned else "")
    if args.sql_out:
        args.sql_out.write_text(generated, encoding="utf-8")
    if args.dry_run:
        print(f"Cluster mode: {cluster}; tables: {len(tables)}; selected views: {len(view_plan)}")
        print(generated, end="")
        return

    client = make_client()
    try:
        database = os.environ.get("CLICKHOUSE_DB", "default")
        ensure_ledger(client, cluster, cluster_name)
        applied = applied_checksums(client)

        for table in tables:
            sync_table(client, table, cluster, cluster_name, database, applied)
            print(f"Synchronized table {table.attrib['name']}")

        for ident, sql in view_plan:
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if applied.get(ident) == checksum:
                continue
            client.command(sql)
            record_applied(client, ident, checksum)
            applied[ident] = checksum
            print(f"Synchronized {ident}")

        print(f"ClickHouse schema synchronization completed ({'clustered' if cluster else 'unclustered'}).")
    finally:
        client.close()


if __name__ == "__main__":
    main()
