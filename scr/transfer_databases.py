from datetime import timedelta, date
import datetime
from pymongo import MongoClient
import time
from tqdm import tqdm

def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days)):
        yield start_date + timedelta(n)

local_client = MongoClient("localhost:27017")
cura_client = MongoClient('mongodb://root:NMxySSPMXDesV9ITwUVr@transit-access.app.cura.osu.edu:9000/?authSource=admin&ssl=true')

# # Transfer GTFS stops
# print("GTFS Start.")
# db_time_stamps_set = set()
# db_time_stamps = []
# db_GTFS = local_client.cota_gtfs
# raw_stamps = db_GTFS.list_collection_names()
# for each_raw in raw_stamps:
#     each_raw = int(each_raw.split("_")[0])
#     db_time_stamps_set.add(each_raw)

# for each_raw in db_time_stamps_set:
#     db_time_stamps.append(each_raw)
# db_time_stamps.sort()

# for each_date in tqdm(db_time_stamps):
#     rl_stop = list(db_GTFS[str(each_date) + "_stops"].find({}))
#     for i in rl_stop:
#         i.pop("_id")
#     cura_client.cota_gtfs[str(each_date) + "_stops"].insert_many(rl_stop)

# Transfer real-time collections
print("Real-time Start.")
dates = list(daterange(date(2018, 9, 14), date(2021, 11, 23)))
for each_date in tqdm(dates):
    today_date = each_date.strftime("%Y%m%d")
    rl_realtime = list(local_client.cota_real_time["R" + today_date].find({}))
    for j in rl_realtime:
        j.pop("_id")
    if rl_realtime != None and rl_realtime != []:
        cura_client.cota_real_time["R" + today_date].insert_many(rl_realtime)