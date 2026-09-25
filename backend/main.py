from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "route53.db"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_database() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hosted_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                zone_type TEXT NOT NULL DEFAULT 'Public hosted zone',
                record_count INTEGER NOT NULL DEFAULT 0,
                comment TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_id INTEGER NOT NULL REFERENCES hosted_zones(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                value TEXT NOT NULL,
                ttl INTEGER NOT NULL DEFAULT 300,
                routing_policy TEXT NOT NULL DEFAULT 'Simple',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traffic_policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                policy_type TEXT NOT NULL DEFAULT 'Latency-based',
                status TEXT NOT NULL DEFAULT 'Active',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS health_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                check_type TEXT NOT NULL DEFAULT 'HTTPS',
                status TEXT NOT NULL DEFAULT 'Healthy',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS resolver_endpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'Outbound',
                status TEXT NOT NULL DEFAULT 'Operational',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Active',
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO users(email, name, created_at) VALUES (?, ?, ?)",
            ("demo@route53.local", "Demo User", now()),
        )
        if connection.execute("SELECT COUNT(*) FROM hosted_zones").fetchone()[0] == 0:
            zone_id = connection.execute(
                "INSERT INTO hosted_zones(name, comment, created_at) VALUES (?, ?, ?)",
                ("example.com", "Primary website zone", now()),
            ).lastrowid
            connection.executemany(
                "INSERT INTO records(zone_id, name, type, value, ttl, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (zone_id, "example.com", "A", "192.0.2.10", 300, now()),
                    (zone_id, "www.example.com", "CNAME", "example.com", 300, now()),
                    (zone_id, "example.com", "NS", "ns-123.awsdns-45.org.", 172800, now()),
                    (zone_id, "example.com", "SOA", "ns-123.awsdns-45.org. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 86400", 900, now()),
                ],
            )
        connection.execute(
            "UPDATE hosted_zones SET record_count = (SELECT COUNT(*) FROM records WHERE records.zone_id = hosted_zones.id)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO traffic_policies(name, policy_type, status, created_at) VALUES (?, ?, ?, ?)",
            ("Primary website routing", "Latency-based", "Active", now()),
        )
        if connection.execute("SELECT COUNT(*) FROM health_checks").fetchone()[0] == 0:
            connection.execute(
                "INSERT INTO health_checks(name, endpoint, check_type, status, created_at) VALUES (?, ?, ?, ?, ?)",
                ("Website endpoint", "https://example.com/health", "HTTPS", "Healthy", now()),
            )
        connection.execute(
            "INSERT OR IGNORE INTO resolver_endpoints(name, direction, status, created_at) VALUES (?, ?, ?, ?)",
            ("Default outbound resolver", "Outbound", "Operational", now()),
        )
        connection.execute(
            "INSERT OR IGNORE INTO profiles(name, description, status, created_at) VALUES (?, ?, ?, ?)",
            ("Default profile", "Shared DNS settings for the demo environment", "Active", now()),
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Route53 Clone API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    email: str = "demo@route53.local"


class ZoneInput(BaseModel):
    name: str = Field(min_length=1)
    comment: str = ""
    zone_type: str = "Public hosted zone"


class RecordInput(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(pattern="^(A|AAAA|CNAME|TXT|MX|NS|PTR|SRV|CAA|SOA)$")
    value: str = Field(min_length=1)
    ttl: int = Field(default=300, ge=0, le=2147483647)
    routing_policy: str = "Simple"


class TrafficPolicyInput(BaseModel):
    name: str = Field(min_length=1)
    policy_type: str = "Latency-based"


class HealthCheckInput(BaseModel):
    name: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    check_type: str = "HTTPS"


class ResolverEndpointInput(BaseModel):
    name: str = Field(min_length=1)
    direction: str = "Outbound"


class ProfileInput(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def find_zone(connection: sqlite3.Connection, zone_id: int) -> sqlite3.Row:
    zone = connection.execute("SELECT * FROM hosted_zones WHERE id = ?", (zone_id,)).fetchone()
    if zone is None:
        raise HTTPException(status_code=404, detail="Hosted zone not found")
    return zone


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    with connect() as connection:
        counts = {
            "hosted_zones": connection.execute("SELECT COUNT(*) FROM hosted_zones").fetchone()[0],
            "records": connection.execute("SELECT COUNT(*) FROM records").fetchone()[0],
            "traffic_policies": connection.execute("SELECT COUNT(*) FROM traffic_policies").fetchone()[0],
            "health_checks": connection.execute("SELECT COUNT(*) FROM health_checks").fetchone()[0],
            "resolver_endpoints": connection.execute("SELECT COUNT(*) FROM resolver_endpoints").fetchone()[0],
            "profiles": connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0],
        }
    return {"counts": counts, "api_status": "Connected", "database": "SQLite"}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    with connect() as connection:
        user = connection.execute("SELECT * FROM users WHERE email = ?", (payload.email,)).fetchone()
    if user is None:
        raise HTTPException(status_code=401, detail="Use the demo account to sign in")
    return {"token": "mock-session-token", "user": row_dict(user)}


@app.post("/api/auth/logout")
def logout() -> dict[str, bool]:
    return {"success": True}


@app.get("/api/auth/me")
def me() -> dict[str, Any]:
    with connect() as connection:
        return row_dict(connection.execute("SELECT * FROM users LIMIT 1").fetchone())


@app.get("/api/zones")
def list_zones(search: str = Query(default="")) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM hosted_zones WHERE name LIKE ? OR comment LIKE ? ORDER BY name",
            (f"%{search}%", f"%{search}%"),
        ).fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/zones", status_code=201)
def create_zone(payload: ZoneInput) -> dict[str, Any]:
    name = payload.name.rstrip(".") + "."
    with connect() as connection:
        existing = connection.execute("SELECT id FROM hosted_zones WHERE name = ?", (name,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="A hosted zone with this name already exists")
        zone_id = connection.execute(
            "INSERT INTO hosted_zones(name, zone_type, comment, created_at) VALUES (?, ?, ?, ?)",
            (name, payload.zone_type, payload.comment, now()),
        ).lastrowid
        zone = find_zone(connection, zone_id)
    return row_dict(zone)


@app.patch("/api/zones/{zone_id}")
def update_zone(zone_id: int, payload: ZoneInput) -> dict[str, Any]:
    with connect() as connection:
        find_zone(connection, zone_id)
        connection.execute(
            "UPDATE hosted_zones SET name = ?, zone_type = ?, comment = ? WHERE id = ?",
            (payload.name.rstrip(".") + ".", payload.zone_type, payload.comment, zone_id),
        )
        return row_dict(find_zone(connection, zone_id))


@app.delete("/api/zones/{zone_id}")
def delete_zone(zone_id: int) -> dict[str, bool]:
    with connect() as connection:
        find_zone(connection, zone_id)
        connection.execute("DELETE FROM hosted_zones WHERE id = ?", (zone_id,))
    return {"success": True}


@app.get("/api/zones/{zone_id}/records")
def list_records(zone_id: int, search: str = Query(default=""), record_type: str = Query(default="")) -> list[dict[str, Any]]:
    with connect() as connection:
        find_zone(connection, zone_id)
        rows = connection.execute(
            """SELECT * FROM records WHERE zone_id = ? AND (name LIKE ? OR value LIKE ?)
            AND (? = '' OR type = ?) ORDER BY name, type""",
            (zone_id, f"%{search}%", f"%{search}%", record_type, record_type),
        ).fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/zones/{zone_id}/records", status_code=201)
def create_record(zone_id: int, payload: RecordInput) -> dict[str, Any]:
    with connect() as connection:
        find_zone(connection, zone_id)
        record_id = connection.execute(
            "INSERT INTO records(zone_id, name, type, value, ttl, routing_policy, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (zone_id, payload.name, payload.type, payload.value, payload.ttl, payload.routing_policy, now()),
        ).lastrowid
        connection.execute("UPDATE hosted_zones SET record_count = record_count + 1 WHERE id = ?", (zone_id,))
        return row_dict(connection.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone())


@app.patch("/api/zones/{zone_id}/records/{record_id}")
def update_record(zone_id: int, record_id: int, payload: RecordInput) -> dict[str, Any]:
    with connect() as connection:
        find_zone(connection, zone_id)
        record = connection.execute("SELECT * FROM records WHERE id = ? AND zone_id = ?", (record_id, zone_id)).fetchone()
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        connection.execute(
            "UPDATE records SET name = ?, type = ?, value = ?, ttl = ?, routing_policy = ? WHERE id = ?",
            (payload.name, payload.type, payload.value, payload.ttl, payload.routing_policy, record_id),
        )
        return row_dict(connection.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone())


@app.delete("/api/zones/{zone_id}/records/{record_id}")
def delete_record(zone_id: int, record_id: int) -> dict[str, bool]:
    with connect() as connection:
        find_zone(connection, zone_id)
        deleted = connection.execute("DELETE FROM records WHERE id = ? AND zone_id = ?", (record_id, zone_id)).rowcount
        if not deleted:
            raise HTTPException(status_code=404, detail="Record not found")
        connection.execute("UPDATE hosted_zones SET record_count = MAX(record_count - 1, 0) WHERE id = ?", (zone_id,))
    return {"success": True}


@app.get("/api/traffic-policies")
def list_traffic_policies() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM traffic_policies ORDER BY name").fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/traffic-policies", status_code=201)
def create_traffic_policy(payload: TrafficPolicyInput) -> dict[str, Any]:
    try:
        with connect() as connection:
            policy_id = connection.execute(
                "INSERT INTO traffic_policies(name, policy_type, created_at) VALUES (?, ?, ?)",
                (payload.name, payload.policy_type, now()),
            ).lastrowid
            return row_dict(connection.execute("SELECT * FROM traffic_policies WHERE id = ?", (policy_id,)).fetchone())
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="A traffic policy with this name already exists") from error


@app.get("/api/health-checks")
def list_health_checks() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM health_checks ORDER BY name").fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/health-checks", status_code=201)
def create_health_check(payload: HealthCheckInput) -> dict[str, Any]:
    with connect() as connection:
        check_id = connection.execute(
            "INSERT INTO health_checks(name, endpoint, check_type, created_at) VALUES (?, ?, ?, ?)",
            (payload.name, payload.endpoint, payload.check_type, now()),
        ).lastrowid
        return row_dict(connection.execute("SELECT * FROM health_checks WHERE id = ?", (check_id,)).fetchone())


@app.get("/api/resolver/endpoints")
def list_resolver_endpoints() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM resolver_endpoints ORDER BY name").fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/resolver/endpoints", status_code=201)
def create_resolver_endpoint(payload: ResolverEndpointInput) -> dict[str, Any]:
    with connect() as connection:
        endpoint_id = connection.execute(
            "INSERT INTO resolver_endpoints(name, direction, created_at) VALUES (?, ?, ?)",
            (payload.name, payload.direction, now()),
        ).lastrowid
        return row_dict(connection.execute("SELECT * FROM resolver_endpoints WHERE id = ?", (endpoint_id,)).fetchone())


@app.get("/api/profiles")
def list_profiles() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM profiles ORDER BY name").fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/profiles", status_code=201)
def create_profile(payload: ProfileInput) -> dict[str, Any]:
    try:
        with connect() as connection:
            profile_id = connection.execute(
                "INSERT INTO profiles(name, description, created_at) VALUES (?, ?, ?)",
                (payload.name, payload.description, now()),
            ).lastrowid
            return row_dict(connection.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone())
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="A profile with this name already exists") from error
