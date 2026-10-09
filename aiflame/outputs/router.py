"""Fan out admitted task events and keep a separate receipt for every output."""
from copy import deepcopy


class OutputRouter:
    def __init__(self, db, wall):
        self.db, self.wall = db, wall
        self.outputs = {}
        db.execute("""CREATE TABLE IF NOT EXISTS deliveries(
            event TEXT, output TEXT, status TEXT, updated REAL, detail TEXT,
            PRIMARY KEY(event, output))""")
        # A process restart must never claim that an unsubmitted notification arrived.
        db.execute("UPDATE deliveries SET status='interrupted' WHERE status IN ('pending','queued','claimed')")
        db.commit()

    def register(self, output):
        if output.name in self.outputs:
            raise ValueError("Output already registered: " + output.name)
        self.outputs[output.name] = output

    def record(self, event_id, output, status, detail=None):
        self.db.execute("INSERT OR REPLACE INTO deliveries VALUES (?,?,?,?,?)",
                        (event_id, output, status, self.wall(), detail))
        if output == "flame":
            # Compatibility: events.status has always been the lamp receipt.
            self.db.execute("UPDATE events SET status=? WHERE id=?", (status, event_id))
        self.db.commit()

    def validate_target(self, event):
        for output in self.outputs.values():
            validator = getattr(output, "validate_target", None)
            if validator:
                validator(event)

    def dispatch(self, event):
        results = {}
        for name, output in tuple(self.outputs.items()):
            try:
                status = output.accept(deepcopy(event))
                self.record(event["event_id"], name, status)
            except Exception as error:
                # One broken device/provider must not block other outputs.
                status = "failed"
                self.record(event["event_id"], name, status, type(error).__name__)
            results[name] = status
        return results

    def snapshot(self):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM deliveries WHERE event IN (SELECT id FROM events ORDER BY created DESC, rowid DESC LIMIT 50) ORDER BY updated DESC")]
