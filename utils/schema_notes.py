"""
Context that is given to the LLM together with the database schema.

AGENT_TABLES limits which tables the agent sees, so unrelated tables in the
same database don't cost tokens or confuse SQL generation.

DATA_NOTES describes what the data means - the kind of documentation a data
team would write for any analyst. Keep it about the data itself, not about
specific questions, so improvements generalise beyond the evaluation set.
"""

AGENT_TABLES = ["users", "vehicles", "rides", "payments", "ratings"]

DATA_NOTES = """
- users holds both riders and drivers; user_type is 'rider' or 'driver'. A driver is a
  user with user_type = 'driver'. city and province are where the user is based.
- rides.rider_id and rides.driver_id both reference users.user_id.
- rides.status is one of requested, in_progress, completed, cancelled. Only completed
  rides have pickup/dropoff times, distance and fare; other rides have fare 0.
  requested_at is when the ride was requested; dropoff_time is when it was completed.
- vehicles: each driver has one vehicle (vehicles.driver_id -> users.user_id).
- payments: one payment per completed ride, and payments.amount equals the ride's fare.
  payments.payment_status (completed, failed, refunded) is about the payment, not the
  ride; only filter on it when the question is about payments or revenue.
  Revenue means the sum of amount for payments with payment_status = 'completed'.
- ratings: at most one rating per ride, and only completed rides can be rated, but not
  every completed ride has a rating. ratings.driver_id is the driver who was rated.
""".strip()
