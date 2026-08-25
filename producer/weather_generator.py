import random
from datetime import datetime, timezone

STATIONS = [
    {"station_id": "ST001", "city": "Bengaluru"},
    {"station_id": "ST002", "city": "Mysuru"},
    {"station_id": "ST003", "city": "Chennai"},
    {"station_id": "ST004", "city": "Hyderabad"},
    {"station_id": "ST005", "city": "Mumbai"},
]

NORMAL_RANGES = {
    "temperature": (15.0, 35.0),
    "humidity": (40, 90),
    "rainfall": (0.0, 10.0),
    "wind_speed": (5.0, 25.0),
}

EXTREME_RANGES = {
    "temperature": (40.1, 45.0),
    "rainfall": (50.1, 100.0),
    "wind_speed": (60.1, 90.0),
}

EXTREME_CHANCE = 1 / 30


def generate_reading(station, rand=random):
    temperature = round(rand.uniform(*NORMAL_RANGES["temperature"]), 1)
    humidity = rand.randint(*NORMAL_RANGES["humidity"])
    rainfall = round(rand.uniform(*NORMAL_RANGES["rainfall"]), 1)
    wind_speed = round(rand.uniform(*NORMAL_RANGES["wind_speed"]), 1)

    if rand.random() < EXTREME_CHANCE:
        extreme_field = rand.choice(list(EXTREME_RANGES.keys()))
        extreme_value = round(rand.uniform(*EXTREME_RANGES[extreme_field]), 1)
        if extreme_field == "temperature":
            temperature = extreme_value
        elif extreme_field == "rainfall":
            rainfall = extreme_value
        elif extreme_field == "wind_speed":
            wind_speed = extreme_value

    return {
        "station_id": station["station_id"],
        "city": station["city"],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "temperature": temperature,
        "humidity": humidity,
        "rainfall": rainfall,
        "wind_speed": wind_speed,
    }


def generate_all_readings(rand=random):
    return [generate_reading(station, rand=rand) for station in STATIONS]
