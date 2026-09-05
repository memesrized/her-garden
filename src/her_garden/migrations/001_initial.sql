CREATE TABLE entities (
    id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('plant', 'location', 'inventory')),
    state jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX entities_kind_idx ON entities(kind);
CREATE INDEX plant_location_idx ON entities((state->>'location_id')) WHERE kind = 'plant';
CREATE UNIQUE INDEX location_name_idx ON entities(lower(state->>'name')) WHERE kind = 'location';

CREATE TABLE events (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id uuid NOT NULL UNIQUE,
    entity_id uuid NOT NULL REFERENCES entities(id),
    request_id uuid NOT NULL UNIQUE,
    request jsonb NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL,
    supersedes_event_id uuid UNIQUE REFERENCES events(id)
);
CREATE INDEX event_entity_time_idx ON events(entity_id, occurred_at, sequence);
CREATE FUNCTION reject_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Event history is append-only';
END;
$$;
CREATE TRIGGER immutable_events BEFORE UPDATE OR DELETE ON events
FOR EACH ROW EXECUTE FUNCTION reject_event_mutation();

-- OAuth values are private server data; bearer credentials are stored by hash.
CREATE TABLE oauth_records (
    kind text NOT NULL,
    key text NOT NULL,
    value jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY(kind, key)
);
CREATE INDEX oauth_expiry_idx ON oauth_records(expires_at);
