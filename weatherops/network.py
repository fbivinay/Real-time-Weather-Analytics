"""Real places the ShopFlow network is built on: 40 Indian cities and the
logistics clusters (warehouse parks) that serve them. Everything synthetic -
warehouses, routes, orders - is derived from these in weatherops.company.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    id: str
    name: str
    state: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Hub:
    id: str
    name: str
    city_id: str
    lat: float
    lon: float


CITIES = tuple(City(*c) for c in [
    ("DEL", "Delhi", "Delhi", 28.6139, 77.2090),
    ("JAI", "Jaipur", "Rajasthan", 26.9124, 75.7873),
    ("JOD", "Jodhpur", "Rajasthan", 26.2389, 73.0243),
    ("LKO", "Lucknow", "Uttar Pradesh", 26.8467, 80.9462),
    ("AGR", "Agra", "Uttar Pradesh", 27.1767, 78.0081),
    ("VNS", "Varanasi", "Uttar Pradesh", 25.3176, 82.9739),
    ("CHD", "Chandigarh", "Chandigarh", 30.7333, 76.7794),
    ("LDH", "Ludhiana", "Punjab", 30.9010, 75.8573),
    ("DDN", "Dehradun", "Uttarakhand", 30.3165, 78.0322),
    ("AMD", "Ahmedabad", "Gujarat", 23.0225, 72.5714),
    ("SRT", "Surat", "Gujarat", 21.1702, 72.8311),
    ("BRD", "Vadodara", "Gujarat", 22.3072, 73.1812),
    ("RJK", "Rajkot", "Gujarat", 22.3039, 70.8022),
    ("MUM", "Mumbai", "Maharashtra", 19.0760, 72.8777),
    ("PUN", "Pune", "Maharashtra", 18.5204, 73.8567),
    ("NSK", "Nashik", "Maharashtra", 19.9975, 73.7898),
    ("NAG", "Nagpur", "Maharashtra", 21.1458, 79.0882),
    ("IDR", "Indore", "Madhya Pradesh", 22.7196, 75.8577),
    ("BPL", "Bhopal", "Madhya Pradesh", 23.2599, 77.4126),
    ("GOA", "Panaji", "Goa", 15.4909, 73.8278),
    ("BLR", "Bengaluru", "Karnataka", 12.9716, 77.5946),
    ("MYS", "Mysuru", "Karnataka", 12.2958, 76.6394),
    ("MLR", "Mangaluru", "Karnataka", 12.9141, 74.8560),
    ("HBL", "Hubballi", "Karnataka", 15.3647, 75.1240),
    ("CHE", "Chennai", "Tamil Nadu", 13.0827, 80.2707),
    ("CBE", "Coimbatore", "Tamil Nadu", 11.0168, 76.9558),
    ("MDU", "Madurai", "Tamil Nadu", 9.9252, 78.1198),
    ("KOC", "Kochi", "Kerala", 9.9312, 76.2673),
    ("TVM", "Thiruvananthapuram", "Kerala", 8.5241, 76.9366),
    ("HYD", "Hyderabad", "Telangana", 17.3850, 78.4867),
    ("VJA", "Vijayawada", "Andhra Pradesh", 16.5062, 80.6480),
    ("VSK", "Visakhapatnam", "Andhra Pradesh", 17.6868, 83.2185),
    ("NLR", "Nellore", "Andhra Pradesh", 14.4426, 79.9865),
    ("KOL", "Kolkata", "West Bengal", 22.5726, 88.3639),
    ("SLG", "Siliguri", "West Bengal", 26.7271, 88.3953),
    ("BBS", "Bhubaneswar", "Odisha", 20.2961, 85.8245),
    ("PAT", "Patna", "Bihar", 25.5941, 85.1376),
    ("RAN", "Ranchi", "Jharkhand", 23.3441, 85.3096),
    ("RPR", "Raipur", "Chhattisgarh", 21.2514, 81.6296),
    ("GUW", "Guwahati", "Assam", 26.1445, 91.7362),
])

# Logistics clusters that actually serve 25 of the cities (warehouse parks
# outside the centre). The id is the city id.
HUBS = tuple(Hub(c, name, c, lat, lon) for c, name, lat, lon in [
    ("DEL", "Bilaspur (Gurugram)", 28.3020, 76.8890),
    ("MUM", "Bhiwandi", 19.2813, 73.0483),
    ("BLR", "Hoskote", 13.0707, 77.7982),
    ("CHE", "Sriperumbudur", 12.9675, 79.9419),
    ("HYD", "Shamshabad", 17.2543, 78.4290),
    ("KOL", "Dankuni", 22.6800, 88.2900),
    ("AMD", "Changodar", 22.9300, 72.4400),
    ("PUN", "Chakan", 18.7600, 73.8600),
    ("JAI", "Sitapura", 26.7800, 75.8300),
    ("LKO", "Chinhat", 26.8800, 81.0500),
    ("NAG", "MIHAN", 21.0900, 79.0400),
    ("IDR", "Pithampur", 22.6100, 75.6800),
    ("PAT", "Fatuha", 25.5100, 85.3100),
    ("BBS", "Khurda", 20.1800, 85.6200),
    ("GUW", "Amingaon", 26.1900, 91.6700),
    ("KOC", "Kalamassery", 10.0500, 76.3200),
    ("CBE", "Sulur", 11.0300, 77.1300),
    ("VSK", "Anakapalli", 17.6900, 83.0000),
    ("VJA", "Gannavaram", 16.5400, 80.8000),
    ("CHD", "Zirakpur", 30.6400, 76.8200),
    ("SRT", "Kamrej", 21.2700, 72.9600),
    ("RPR", "Urla", 21.3200, 81.6000),
    ("RAN", "Tupudana", 23.2800, 85.3300),
    ("BPL", "Mandideep", 23.1000, 77.5300),
    ("VNS", "Raja Talab", 25.2800, 82.8500),
])
