CREATE TABLE watering_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    reminder_time time NOT NULL
);
INSERT INTO watering_settings (singleton, reminder_time) VALUES (true, '09:00');

CREATE TABLE watering_schedules (
    plant_id uuid PRIMARY KEY REFERENCES entities(id),
    anchor_date date NOT NULL,
    cadence_days integer NOT NULL CHECK (cadence_days BETWEEN 1 AND 3650),
    reminder_time time NOT NULL,
    next_due_at timestamptz NOT NULL,
    manual_override boolean NOT NULL DEFAULT false,
    enabled boolean NOT NULL DEFAULT true,
    active_cycle_id uuid,
    active_due_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((active_cycle_id IS NULL) = (active_due_at IS NULL))
);
CREATE INDEX watering_due_idx ON watering_schedules(next_due_at);

CREATE TABLE watering_requests (
    request_id uuid PRIMARY KEY,
    request jsonb NOT NULL,
    result jsonb NOT NULL
);

CREATE TABLE telegram_recipients (
    username text PRIMARY KEY,
    chat_id bigint NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE watering_notifications (
    id uuid PRIMARY KEY,
    plant_id uuid NOT NULL REFERENCES watering_schedules(plant_id),
    username text NOT NULL REFERENCES telegram_recipients(username),
    chat_id bigint NOT NULL,
    cycle_id uuid NOT NULL,
    due_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'cancelled')),
    sent_at timestamptz,
    telegram_message_id bigint,
    UNIQUE (plant_id, username, cycle_id)
);
CREATE INDEX watering_pending_idx ON watering_notifications(due_at) WHERE status = 'pending';
