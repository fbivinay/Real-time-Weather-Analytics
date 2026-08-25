ALERT_THRESHOLDS = {
    "heat": {"field": "temperature", "threshold": 40},
    "heavy_rain": {"field": "rainfall", "threshold": 50},
    "high_wind": {"field": "wind_speed", "threshold": 60},
}


def check_alert(reading):
    for alert_type, rule in ALERT_THRESHOLDS.items():
        field = rule["field"]
        threshold = rule["threshold"]
        value = reading[field]
        if value > threshold:
            return {
                "record_type": "alert",
                "station_id": reading["station_id"],
                "city": reading["city"],
                "timestamp": reading["timestamp"],
                "alert_type": alert_type,
                "field": field,
                "value": value,
                "threshold": threshold,
            }
    return None
