"""
Context that is given to the LLM together with the database schema.

Since EXP-16 the agent can work on more than one dataset: AGENT_DATASET=rides (the default, the
sample ride-sharing data in "public") or AGENT_DATASET=nyc (one month of real NYC Uber/Lyft trips,
loaded by utils/load_nyc_trips.py into the "nyc" schema).

AGENT_TABLES limits which tables the agent sees, so unrelated tables in the
same database don't cost tokens or confuse SQL generation.

DATA_NOTES describes what the data means - the kind of documentation a data
team would write for any analyst. Keep it about the data itself, not about
specific questions, so improvements generalise beyond the evaluation set.
"""

import os


AGENT_TABLES = ["users", "vehicles", "rides", "payments", "ratings"]

# Tables a person approved loading (EXP-13) live here; the agent sees all of them (EXP-14)
EXTERNAL_SCHEMA = "external"

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


# Real data (EXP-16): NYC Taxi & Limousine Commission "High Volume For-Hire Vehicle" trip records.
# Written from the TLC data dictionary and a profile of the loaded month (utils/profile_nyc.py),
# before the NYC questions were run; about the data, not about specific questions.
NYC_NOTES = """
- nyc.trips has one row per Uber or Lyft trip in New York City with a pickup in June 2026
  (about 20.8 million rows). It is the NYC Taxi & Limousine Commission's high volume for-hire
  vehicle trip data.
- hvfhs_license_num identifies the company: join nyc.companies (HV0003 = Uber, HV0005 = Lyft;
  only these two appear).
- pickup_zone_id and dropoff_zone_id reference nyc.zones.zone_id. nyc.zones gives each TLC taxi
  zone's borough and zone name. The airports are zones 'JFK Airport' and 'LaGuardia Airport'
  (borough Queens) and 'Newark Airport' (borough EWR). Boroughs 'N/A' and 'Unknown' are not
  real places.
- Times: request_datetime is when the passenger asked for the ride, on_scene_datetime when the
  driver arrived, pickup_datetime and dropoff_datetime when the trip started and ended. When a
  question doesn't say which time to use, use pickup_datetime.
- trip_miles is the trip distance in miles; trip_time is the trip duration in seconds.
- Money (US dollars): base_passenger_fare is the fare before tolls, tips, taxes and fees.
  tips are what the passenger tipped the driver. driver_pay is what the driver was paid,
  without tolls and tips. tolls, bcf (Black Car Fund), sales_tax, congestion_surcharge,
  airport_fee and cbd_congestion_fee are separate charges. A few thousand trips have a
  negative or zero fare (adjustments); keep them unless the question excludes them.
- The *_flag columns are 'Y' or 'N': shared_request_flag = the passenger asked for a shared
  ride, shared_match_flag = the ride was actually shared with another booking,
  wav_request_flag = a wheelchair-accessible vehicle was requested, wav_match_flag = the trip
  was in one, access_a_ride_flag = the trip was arranged for the MTA's Access-A-Ride.
""".strip()

DATASETS = {
    "rides": {"schema": "public", "tables": AGENT_TABLES, "notes": DATA_NOTES, "qualify_names": False,
              "loaded_data": True,
              "covers": "riders and drivers, their vehicles, rides, payments and ratings"},
    "nyc": {"schema": "nyc", "tables": ["trips", "zones", "companies"], "notes": NYC_NOTES,
            "qualify_names": True, "loaded_data": False,
            "covers": "Uber and Lyft trips in New York City in June 2026, the taxi zones and the companies"},
}


def dataset_name() -> str:
    name = os.getenv("AGENT_DATASET", "rides").strip().lower() or "rides"
    if name not in DATASETS:
        raise ValueError(f"AGENT_DATASET must be one of {', '.join(DATASETS)}, not {name!r}.")
    return name


def current_dataset() -> dict:
    """The dataset the agent answers questions about (AGENT_DATASET, default 'rides')."""
    return DATASETS[dataset_name()]
