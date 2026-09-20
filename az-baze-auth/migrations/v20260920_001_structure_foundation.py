VERSION = "20260920_001"
NAME = "structure_foundation"


def upgrade(conn):
    conn.execute(
        """
        CREATE TABLE holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            short_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','archived'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER NOT NULL,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            short_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','archived')),
            FOREIGN KEY (holding_id) REFERENCES holdings(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','archived')),
            UNIQUE (id, organization_id),
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            cluster_id INTEGER NOT NULL,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            short_name TEXT NOT NULL DEFAULT '',
            region TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            timezone TEXT NOT NULL CHECK (length(trim(timezone)) > 0),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','archived')),
            UNIQUE (id, organization_id),
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
            FOREIGN KEY (cluster_id, organization_id)
                REFERENCES clusters(id, organization_id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE directions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            short_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','archived')),
            display_order INTEGER,
            UNIQUE (id, organization_id),
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE clinic_directions (
            clinic_id INTEGER NOT NULL,
            direction_id INTEGER NOT NULL,
            organization_id INTEGER NOT NULL,
            PRIMARY KEY (clinic_id, direction_id),
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
            FOREIGN KEY (clinic_id, organization_id)
                REFERENCES clinics(id, organization_id) ON DELETE RESTRICT,
            FOREIGN KEY (direction_id, organization_id)
                REFERENCES directions(id, organization_id) ON DELETE RESTRICT
        )
        """
    )

    conn.execute("CREATE INDEX idx_organizations_holding ON organizations(holding_id)")
    conn.execute("CREATE INDEX idx_clusters_organization ON clusters(organization_id)")
    conn.execute("CREATE INDEX idx_clinics_organization ON clinics(organization_id)")
    conn.execute("CREATE INDEX idx_clinics_cluster ON clinics(cluster_id)")
    conn.execute("CREATE INDEX idx_directions_organization ON directions(organization_id)")
    conn.execute(
        "CREATE INDEX idx_clinic_directions_organization ON clinic_directions(organization_id)"
    )
