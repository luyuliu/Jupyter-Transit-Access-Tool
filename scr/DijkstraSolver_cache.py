import BasicSolver
import sys
import os
import time, math
import multiprocessing
import copy
from pymongo import MongoClient
from datetime import timedelta, date, datetime
from math import sin, cos, sqrt, atan2, pi, acos
from tqdm import tqdm
from collections import OrderedDict
import time as atime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transfer_tools
client = MongoClient('mongodb://localhost:27017/')

class DijkstraSolver(BasicSolver.BasicSolver):
    def __init__(self, args, startStopID):
        BasicSolver.BasicSolver.__init__(self)
        timestamp = args[0]
        walkingDistanceLimit = args[1]
        timeDeltaLimit = args[2]
        walkingSpeed = args[3]
        scooterSpeed = args[4]
        scooterDistanceLimit = args[5]
        isRealTime = args[6]
        isScooter = args[7]
        # print(scooterDic)

        self.walkingDistanceLimit = walkingDistanceLimit
        self.scooterDistanceLimit = scooterDistanceLimit

        self.timestamp = timestamp
        self.timeDeltaLimit = timeDeltaLimit
        self.walkingSpeed = walkingSpeed
        self.scooterSpeed = scooterSpeed
        self.isRealTime = isRealTime
        self.walkingTimeLimit = walkingDistanceLimit/walkingSpeed
        self.isScooter = isScooter

        # Time
        self.curDateTime = datetime.fromtimestamp(timestamp)
        self.curDate = self.curDateTime.date()
        todayDate = self.curDate.strftime("%Y%m%d")
        self.todaySeconds = time.mktime(time.strptime(todayDate, "%Y%m%d"))
        self.timeLimit = min(self.todaySeconds + 86400, timestamp + timeDeltaLimit)
        self.todayDate = todayDate
        self.curGTFSTimestamp = self.find_gtfs_time_stamp(self.curDate)

        # print("current GTFS timestamp", self.curGTFSTimestamp)

        # Mongo GTFS setup
        self.db_GTFS = client.cota_gtfs
        self.db_real_time = client.cota_real_time
        self.col_stops = self.db_GTFS[str(self.curGTFSTimestamp) + "_stops"]
        self.col_stop_times = self.db_GTFS[str(
            self.curGTFSTimestamp) + "_stop_times"]
        self.col_real_times = self.db_real_time["R" + todayDate]

        self.db_scooter = client.lime_location
        self.col_scooter = self.db_scooter[todayDate]

        self.rl_stops = list(self.col_stops.find({}))
        self.rl_stop_times = list(self.col_real_times.find({"time": {"$gt": timestamp, "$lt": self.timeLimit}})) if isRealTime else list(
            self.col_real_times.find({"scheduled_time": {"$gt": timestamp, "$lt": self.timeLimit}}))

        self.col_scooter_cache = client.scooter_cache[todayDate]
        
        # print(len(self.rl_stop_times), timestamp, self.timeLimit)

        if self.rl_stop_times != None:
            if isRealTime:
                self.rl_stop_times.sort(key=self.sortrlStopTimesRealtime)
            else:
                self.rl_stop_times.sort(key=self.sortrlStopTimesSchedule)

        self.stopsDic = {}
        self.visitedSet = {}
        self.timeListByStop = {}
        self.timeListByTrip = {}
        self.arcsDic = {}

        for eachStop in self.rl_stops:
            self.stopsDic[eachStop["stop_id"]] = eachStop

        for eachTime in self.rl_stop_times:
            stopID = eachTime["stop_id"]
            tripID = eachTime["trip_id"]
            try:
                self.timeListByStop[stopID]
            except:
                self.timeListByStop[stopID] = []

            self.timeListByStop[stopID].append(eachTime)

            try:
                self.timeListByTrip[tripID]
            except:
                self.timeListByTrip[tripID] = []

            self.timeListByTrip[tripID].append(eachTime)
        

        for eachTripID, eachTrip in self.timeListByTrip.items():
            if len(eachTrip) < 2:
                continue
            for index in range(0, len(eachTrip)-1):
                generatingStopID = eachTrip[index]["stop_id"]
                receivingStopID = eachTrip[index + 1]["stop_id"]
                try:
                    self.arcsDic[generatingStopID]
                except:
                    self.arcsDic[generatingStopID] = {}

                try:
                    self.arcsDic[generatingStopID][receivingStopID]
                except:
                    self.arcsDic[generatingStopID][receivingStopID] = {}

                timeGen = eachTrip[index]["time"] if self.isRealTime else eachTrip[index]["scheduled_time"]
                timeRec = eachTrip[index +
                                   1]["time"] if self.isRealTime else eachTrip[index + 1]["scheduled_time"]

                try:
                    self.arcsDic[generatingStopID][receivingStopID][timeGen]
                except:
                    self.arcsDic[generatingStopID][receivingStopID][timeGen] = {
                        'time_gen': timeGen, 'time_rec': timeRec, 'bus_time': - timeGen + timeRec, "trip_id": eachTripID}
                

        
        # Sort the arcsDics with OrderedDic.
        for startStopID, startStop in self.arcsDic.items():
            for endStopID, endStop in startStop.items():
                self.arcsDic[startStopID][endStopID] = OrderedDict(sorted(endStop.items()))

        # Import closest scooters from the cache
        self.scooterDic = {}
        rl_scooters = client.scooter_cache[todayDate].find()
        for eachRecord in rl_scooters:
            stopID = eachRecord["stop_id"]
            eachRecord.pop("_id")
            eachRecord.pop("stop_id")
            self.scooterDic[stopID] = {}
            for ts, scooterRecord in eachRecord.items():
                ts = int(ts)
                self.scooterDic[stopID][ts] = scooterRecord
            self.scooterDic[stopID] = OrderedDict(sorted(self.scooterDic[stopID].items()))

        self.count = 0

    def sortrlStopTimesSchedule(self, value):
        return value["scheduled_time"]

    def sortrlStopTimesRealtime(self, value):
        return value["time"]

    def calculateDistance(self, latlng1, latlng2, isOG=True):
        R = 6373
        if isOG:
            lat1 = float(latlng1["stop_lat"])
            lon1 = float(latlng1["stop_lon"])
        else:
            lat1 = float(latlng1["latitude"])
            lon1 = float(latlng1["longitude"])

        lat2 = float(latlng2["stop_lat"])
        lon2 = float(latlng2["stop_lon"])

        theta = lon1 - lon2
        radtheta = pi * theta / 180
        radlat1 = pi * lat1 / 180
        radlat2 = pi * lat2 / 180

        dist = sin(radlat1) * sin(radlat2) + cos(radlat1) * \
            cos(radlat2) * cos(radtheta)
        try:
            dist = acos(dist)
        except:
            dist = 0
        dist = dist * 180 / pi * 60 * 1.1515 * 1609.344

        return dist

    def findClosestStop(self, isOG=False):
        minDistance = sys.maxsize
        closestStop = False
        for eachStop in self.rl_stops:
            eachStopID = eachStop["stop_id"]
            # print(minDistance, self.visitedSet[eachStopID]['time'])
            # if self.visitedSet[eachStopID]['time'] < minDistance:
            #     print(eachStopID, self.visitedSet[eachStopID]['visitTag'])
            if not isOG:
                if self.visitedSet[eachStopID]['time'] < minDistance and self.visitedSet[eachStopID]['visitTag'] == False:
                    minDistance = self.visitedSet[eachStopID]['time']
                    closestStop = eachStop
            else:
                if self.visitedSet[eachStopID]['timeOG'] < minDistance and self.visitedSet[eachStopID]['visitTagOG'] == False:
                    minDistance = self.visitedSet[eachStopID]['timeOG']
                    closestStop = eachStop
        # print(closestStop)
        return closestStop

    def getTravelTime(self, closestStopID, eachStopID, aStartTime):
        # With bus trip, can wait and bus or walk and scooter.
        # All methods are exclusive between two stops. Options:
        # 1. bus + wait. For all arcs.
        # 2. walk. 
        # 3. walk + scooter. (only for startStop arcs, aka first-mile, and last-mile)

        # Risk1: may incur meaningless transfers; but we are not controlling that anyway. Possible solution: control trip_id or transfer_count
        # Risk2: how to deal with last-mile scooters? Mimic the first version.
        # Risk3: how to distinguish the first-mile trip? if timestamp is the starttimestamp, propagate using scooter; if not, propagate using walking
        travelTime = {
            "generatingStopID": closestStopID,
            "receivingStopID": eachStopID,
            "time": sys.maxsize,
            "scooterTime": None,
            "walkTime": None,
            "busTime": None,
            "waitTime": None,
            "tripID": None,
            "scooterID": None,
            "increment": None,
            "tripType": None,
            "lastScooterWalkTime": None,
            "lastScooterScooterTime": None,
            "lastNonScooterWalkTime": None
        }

        closestStop = self.stopsDic[closestStopID]
        eachStop = self.stopsDic[eachStopID]

        # 1. Propagate with bus and wait. Not controlling transfer count. Select the most recent trip.
        # More greedy: if there is a bus trip, wait for that regardless of the expected saved time by walking.
        try:
            self.arcsDic[closestStopID][eachStopID]
        except:
            pass # Not bus trip
        else:
            for eachIndex, eachTrip in self.arcsDic[closestStopID][eachStopID].items():
                if eachTrip["time_gen"] >= aStartTime:
                    recentBusTime = eachTrip["time_gen"]
                    travelTime["tripID"] = eachTrip["trip_id"]
                    travelTime["busTime"] = eachTrip["bus_time"]
                    travelTime["waitTime"] = recentBusTime - aStartTime
                    travelTime["walkTime"] = 0
                    travelTime["scooterTime"] = 0
                    travelTime["time"] = travelTime["busTime"] + travelTime["waitTime"]
                    travelTime["tripType"] = "bus"
                    break
            return travelTime
            
        # 2. Propagate with walk
        # No edge, can only walk/scooter
        if self.visitedSet[closestStopID]["lastTripType"] == "walk" or self.visitedSet[closestStopID]["lastTripType"] == "scooter": # cannot make two subsequent non-transit arcs.
            return travelTime
        dist = self.calculateDistance(self.stopsDic[closestStopID], self.stopsDic[eachStopID]) # Can be precalculated
        walkTime_walk = dist/self.walkingSpeed
        totalTime_walk = walkTime_walk

        if totalTime_walk > self.walkingTimeLimit:
            pass
        elif travelTime["time"] > totalTime_walk: # if no bus trip, travelTime["time"] should be maxsize; or, the bus trip is very slow, but very unlikely
            travelTime["busTime"] = 0
            travelTime["waitTime"] = 0
            travelTime["scooterTime"] = 0
            travelTime["walkTime"] = walkTime_walk
            travelTime["time"] = totalTime_walk
            travelTime["tripType"] = "walk"

        # # 3. Propagate with walk and scooter (but only update for startStop)
        # if dist < self.scooterDistanceLimit + self.walkingDistanceLimit: # Limit the query count; if the dist is larger than scooter distance limit and walking limit combined, then the scooter trip is not possible.
        #     tsList = list(self.scooterDic[closestStopID].keys())
        #     if tsList == []:
        #         return travelTime # No available scooters
        #     ts = None
        #     for ats in tsList:
        #         if ats > aStartTime:
        #             if ts == None:
        #                 ts = ats
        #             if ats > aStartTime + 300: # 5 minutes
        #                 return travelTime # There are scooters but it won't be available until much later.
        #             break
        #         ts = ats
                
        #     surroundingScootersList = self.scooterDic[closestStopID][ts]
            
        #     scooterID = None
        #     increment = None
        #     walkTime_scooter = None
        #     scooterTime_scooter = None

        #     if surroundingScootersList != None:
        #         closestScooter = surroundingScootersList # Select the closest one.
        #         # for closestScooter in surroundingScootersList:
        #         walkDist_scooter = self.calculateDistance(closestScooter, closestStop, False)
        #         scooterDist_scooter = self.calculateDistance(closestScooter, eachStop, False)
        #         if scooterDist_scooter > closestScooter["meter_range"]: # If out of power
        #             walkDist_scooter +=  scooterDist_scooter - closestScooter["meter_range"]
        #             scooterDist_scooter = closestScooter["meter_range"]
        #         if walkDist_scooter > self.walkingDistanceLimit or scooterDist_scooter > self.scooterDistanceLimit: # If out of walking limit
        #             return travelTime
        #         scooterTime_scooter = scooterDist_scooter/self.scooterSpeed
        #         walkTime_scooter = walkDist_scooter/self.walkingSpeed
        #         totalTime_scooter = scooterTime_scooter + walkTime_scooter
        #         scooterID = closestScooter["new_id"]
        #         increment = totalTime_walk - totalTime_scooter
            
        #     if increment is not None:
        #         if increment > 0: # Increment is positive
        #             travelTime["scooterID"] = scooterID
        #             travelTime["increment"] = increment # This is meant to substracted from the total travel time to simulate the last mile.         
        #             if aStartTime == self.timestamp: # first mile; only record this in the travelTime in the first mile.
        #                 travelTime["busTime"] = 0
        #                 travelTime["waitTime"] = 0
        #                 travelTime["scooterTime"] = scooterTime_scooter
        #                 travelTime["walkTime"] = walkTime_scooter
        #                 travelTime["time"] = totalTime_scooter
        #                 travelTime["tripType"] = "scooter"
        #             else: # last mile; record the last mile increment
        #                 travelTime["lastScooterWalkTime"] = walkTime_scooter
        #                 travelTime["lastScooterScooterTime"] = scooterTime_scooter
        #                 travelTime["lastNonScooterWalkTime"] = totalTime_walk

        return travelTime

    def getTravelTimeOG(self, closestStopID, eachStopID, aStartTime):
        # With bus trip, can wait and bus or walk
        # All methods are exclusive between two stops. Options:
        # 1. bus + wait. For all arcs.
        # 2. walk. 

        # Risk1: may incur meaningless transfers; but we are not controlling that anyway. Possible solution: control trip_id or transfer_count
        # Risk2: how to deal with last-mile scooters? Mimic the first version.
        # Risk3: how to distinguish the first-mile trip? if timestamp is the starttimestamp, propagate using scooter; if not, propagate using walking
        travelTime = {
            "generatingStopIDOG": closestStopID,
            "receivingStopID": eachStopID,
            "timeOG": sys.maxsize,
            "walkTimeOG": None,
            "busTimeOG": None,
            "waitTimeOG": None,
            "tripIDOG": None,
            "tripTypeOG": None
        }

        closestStop = self.stopsDic[closestStopID]
        eachStop = self.stopsDic[eachStopID]

        # 1. Propagate with bus and wait. Not controlling transfer count. Select the most recent trip.
        # More greedy: if there is a bus trip, wait for that regardless of the expected saved time by walking.
        try:
            self.arcsDic[closestStopID][eachStopID]
        except:
            pass # Not bus trip
        else:
            for eachIndex, eachTrip in self.arcsDic[closestStopID][eachStopID].items():
                if eachTrip["time_gen"] >= aStartTime:
                    recentBusTime = eachTrip["time_gen"]
                    travelTime["tripIDOG"] = eachTrip["trip_id"]
                    travelTime["busTimeOG"] = eachTrip["bus_time"]
                    travelTime["waitTimeOG"] = recentBusTime - aStartTime
                    travelTime["walkTimeOG"] = 0
                    travelTime["timeOG"] = travelTime["busTimeOG"] + travelTime["waitTimeOG"]
                    travelTime["tripTypeOG"] = "bus"
                    break
            return travelTime
            
        # 2. Propagate with walk
        # No edge, can only walk/scooter
        if self.visitedSet[closestStopID]["lastTripTypeOG"] == "walk" or self.visitedSet[closestStopID]["lastTripTypeOG"] == "scooter": # cannot make two subsequent non-transit arcs.
            return travelTime
        dist = self.calculateDistance(self.stopsDic[closestStopID], self.stopsDic[eachStopID]) # Can be precalculated
        walkTime_walk = dist/self.walkingSpeed
        totalTime_walk = walkTime_walk

        if totalTime_walk > self.walkingTimeLimit:
            pass
        elif travelTime["timeOG"] > totalTime_walk: # if no bus trip, travelTime["time"] should be maxsize; or, the bus trip is very slow, but very unlikely
            travelTime["busTimeOG"] = 0
            travelTime["waitTimeOG"] = 0
            travelTime["walkTimeOG"] = walkTime_walk
            travelTime["timeOG"] = totalTime_walk
            travelTime["tripTypeOG"] = "walk"

        return travelTime

    def addToTravelTime(self, originalStrucuture, closestStructure, travelTime, tempStartTimestamp, isOG=False):
        if not isOG:
            originalStrucuture["time"] = closestStructure["time"] + travelTime["time"]
            originalStrucuture["scooterTime"] = closestStructure["scooterTime"] + travelTime["scooterTime"]
            originalStrucuture["walkTime"] = closestStructure["walkTime"] + travelTime["walkTime"]
            originalStrucuture["busTime"] = closestStructure["busTime"] + travelTime["busTime"]
            originalStrucuture["waitTime"] = closestStructure["waitTime"] + travelTime["waitTime"]
            
            if travelTime["tripType"] == "bus" and travelTime["tripID"] != originalStrucuture["lastTripID"]:
                originalStrucuture["transferCount"] += 1

            originalStrucuture["lastTripType"] = travelTime["tripType"]
            originalStrucuture["lastTripID"] = travelTime["tripID"]
            originalStrucuture["generatingStopID"] = travelTime["generatingStopID"]
            if tempStartTimestamp == self.timestamp:
                originalStrucuture["firstScooterID"] = travelTime['scooterID']
                originalStrucuture["firstScooterIncrement"] = travelTime["increment"]
            else:
                originalStrucuture["lastScooterID"] = travelTime['scooterID']
                originalStrucuture["lastScooterIncrement"] = travelTime["increment"]
                originalStrucuture["lastScooterWalkTime"] = travelTime["lastScooterWalkTime"]
                originalStrucuture["lastScooterScooterTime"] = travelTime["lastScooterScooterTime"]
                originalStrucuture["lastNonScooterWalkTime"] = travelTime["lastNonScooterWalkTime"]
                originalStrucuture["firstScooterID"] = closestStructure['firstScooterID']
                originalStrucuture["firstScooterIncrement"] = closestStructure["firstScooterIncrement"]
        else:
            originalStrucuture["timeOG"] = closestStructure["timeOG"] + travelTime["timeOG"]
            originalStrucuture["walkTimeOG"] = closestStructure["walkTimeOG"] + travelTime["walkTimeOG"]
            originalStrucuture["busTimeOG"] = closestStructure["busTimeOG"] + travelTime["busTimeOG"]
            originalStrucuture["waitTimeOG"] = closestStructure["waitTimeOG"] + travelTime["waitTimeOG"]
            
            if travelTime["tripTypeOG"] == "bus" and travelTime["tripIDOG"] != originalStrucuture["lastTripIDOG"]:
                originalStrucuture["transferCountOG"] += 1

            originalStrucuture["lastTripTypeOG"] = travelTime["tripTypeOG"]
            originalStrucuture["lastTripIDOG"] = travelTime["tripIDOG"]
            originalStrucuture["generatingStopIDOG"] = travelTime["generatingStopIDOG"]
        
        originalStrucuture["receivingStopID"] = travelTime["receivingStopID"]
        originalStrucuture["stop_lat"] = self.stopsDic[travelTime["receivingStopID"]]["stop_lat"]
        originalStrucuture["stop_lon"] = self.stopsDic[travelTime["receivingStopID"]]["stop_lon"]

        return originalStrucuture


    # Dijkstraly find shortest time to each stop.
    def extendStops(self, startStopID):
        self.aStartStopID = startStopID
        startTimestamp = self.timestamp
        self.visitedSet = {}
        for eachStop in self.rl_stops: # Initialization
            eachStopID = eachStop["stop_id"]
            self.visitedSet[eachStopID] = {
                "startStopID": startStopID,
                "time": sys.maxsize,
                "scooterTime": 0,
                "walkTime": 0,
                "busTime": 0,
                "waitTime": 0,
                "generatingStopID": None,
                "receivingStopID": None,
                "lastTripID": None,
                "lastTripType": None,
                "transferCount": 0,
                "firstScooterID": None,
                "firstScooterIncrement": None,
                "lastScooterID": None,
                "lastScooterIncrement": None,
                "lastScooterWalkTime": None,
                "lastScooterScooterTime": None,
                "lastNonScooterWalkTime": None,
                "visitTag": False,
                "timeOG": sys.maxsize,
                "walkTimeOG": 0,
                "busTimeOG": 0,
                "waitTimeOG": 0,
                "generatingStopIDOG": None,
                "receivingStopIDOG": None,
                "lastTripIDOG": None,
                "lastTripTypeOG": None,
                "transferCountOG": 0,
                "visitTagOG": False
            } # OG is the times of the non-scooter trip. 

        self.visitedSet[startStopID]["time"] = 0 # initialization
        self.visitedSet[startStopID]["timeOG"] = 0 # initialization

        a = time.time()
        # OD and trips with scooters
        for _ in tqdm(self.rl_stops):
            closestStop = self.findClosestStop(False) # Dijkstra's algorithm: find the closest node to the S set, which is the ones having the confirmed clostest distance.
            if closestStop == False:
                # print("break!")
                break
            closestStopID = closestStop["stop_id"]

            self.visitedSet[closestStopID]["visitTag"] = True

            # Can modify self.rl_stops to improve performance.

            for eachStop in (self.rl_stops):
                eachStopID = eachStop["stop_id"]
                if self.visitedSet[eachStopID]["visitTag"] == True: # Only for vertices that not in S.
                    continue
                tempStartTimestamp = startTimestamp + self.visitedSet[closestStopID]["time"]
                travelTime = self.getTravelTime(closestStopID, eachStopID, tempStartTimestamp)
                # if travelTime["tripType"] != None:
                #     print(self.visitedSet[eachStopID]["time"] > self.visitedSet[closestStopID]["time"] + travelTime["time"])
                if travelTime["time"] == None:
                    continue
                if self.visitedSet[eachStopID]["time"] > self.visitedSet[closestStopID]["time"] + travelTime["time"]:
                    self.visitedSet[eachStopID] = self.addToTravelTime(self.visitedSet[eachStopID], self.visitedSet[closestStopID], travelTime, tempStartTimestamp)
                    # print("changed: ", self.visitedSet[eachStopID])
            # print(_["stop_id"], "finished!")
            # break
        # print(self.visitedSet)

        b = time.time()
        # OD and trips without scooters (normal propagation)
        for _ in tqdm(self.rl_stops):
            closestStop = self.findClosestStop(True) # Dijkstra's algorithm: find the closest node to the S set, which is the ones having the confirmed clostest distance.
            if closestStop == False:
                # print("break!")
                break
            closestStopID = closestStop["stop_id"]

            self.visitedSet[closestStopID]["visitTagOG"] = True

            # Can modify self.rl_stops to improve performance.

            for eachStop in (self.rl_stops):
                eachStopID = eachStop["stop_id"]
                if self.visitedSet[eachStopID]["visitTagOG"] == True:
                    continue
                tempStartTimestamp = startTimestamp + self.visitedSet[closestStopID]["timeOG"]
                travelTime = self.getTravelTimeOG(closestStopID, eachStopID, tempStartTimestamp)
                # if travelTime["tripType"] != None:
                #     print(self.visitedSet[eachStopID]["time"] > self.visitedSet[closestStopID]["time"] + travelTime["time"])
                if travelTime["timeOG"] == None:
                    continue
                if self.visitedSet[eachStopID]["timeOG"] > self.visitedSet[closestStopID]["timeOG"] + travelTime["timeOG"]:
                    self.visitedSet[eachStopID] = self.addToTravelTime(self.visitedSet[eachStopID], self.visitedSet[closestStopID], travelTime, tempStartTimestamp, True)
                    # print("changed: ", self.visitedSet[eachStopID])
            # print(_["stop_id"], "finished!")
            # break
        # print(self.visitedSet)
        c = time.time()
        # print(startStopID, c-b, b-a)
        return self.visitedSet

def singleAccessibilitySolve(args, startLocation):
    # print(args, startLocation)
    accessibilitySolver = DijkstraSolver(args, startLocation)
    lenStops = accessibilitySolver.extendStops(startLocation)
    del accessibilitySolver
    return lenStops

def collectiveAccessibilitySolve(args, sampledStopsList):

    curDateTime = datetime.fromtimestamp(args[0])
    curDate = curDateTime.date()
    todayDate = curDate.strftime("%Y%m%d")
    
    curGTFSTimestamp = transfer_tools.find_gtfs_time_stamp(curDate)
    rl_stops = client.cota_gtfs[str(curGTFSTimestamp) + "_stops"].find({})

    # Scooter precalculation
    # print(startStopID, "scooter preparation start!")
    # if col_scooter_cache.estimated_document_count()==0:
        
    # scooterDic = {}
    # col_scooter = client.lime_location[todayDate]
    # ee = time.time()
    # for eachStop in tqdm(rl_stops):
    #     stopID = eachStop["stop_id"]
    #     try:
    #         scooterDic[stopID]
    #     except:
    #         scooterDic[stopID] = {}

    #     allNearbyScooters = list(col_scooter.find({"location": {"$near": {
    #         "$geometry": {
    #             "type": "Point",
    #             "coordinates": [float(eachStop["stop_lon"]), float(eachStop["stop_lat"])]
    #         },
    #         "$maxDistance": walkingDistanceLimit
    #     }},
    #         "ts": {
    #         "$gte": args[0],
    #         "$lt": args[0] + timeDeltaLimit
    #     }}))

    #     for eachTime in allNearbyScooters:
    #         ts = str(eachTime["ts"])
    #         try:
    #             scooterDic[stopID][ts]
    #         except:
    #             scooterDic[stopID][ts] = eachTime
    #         #     scooterDic[stopID][ts] = []
    #         # scooterDic[stopID][ts].append(eachTime)            
        
    #     scooterDic[stopID] = OrderedDict(sorted(scooterDic[stopID].items()))
    
    # client.scooter_cache[todayDate].drop()
    # for eachStopID, eachRecord in scooterDic.items():
    #     eachRecord["stop_id"] = eachStopID
    #     client.scooter_cache[todayDate].insert_one(eachRecord)
    # ff = time.time()
    # print(ff-ee, "preparation done!")

    cores = 30
    total_length = len(sampledStopsList)
    batch = math.ceil(total_length/cores)
    for i in tqdm(list(range(batch))):
        pool = multiprocessing.Pool(processes=cores)
        sub_output = []
        try:
            sub_sampledStopsList = sampledStopsList[cores*i:cores*(i+1)]
        except:
            sub_sampledStopsList = sampledStopsList[cores*i:]
        star_parameters = zip([args]*len(sub_sampledStopsList), sub_sampledStopsList)
        sub_output = pool.starmap(singleAccessibilitySolve, star_parameters)
        pool.close()
        pool.join()
        collectiveInsert(args, sub_output)

def collectiveInsert(args, output):
    timestamp = args[0]
    curDateTime = datetime.fromtimestamp(timestamp)
    curDate = curDateTime.date()
    todayDate = curDate.strftime("%Y%m%d")
    recordCollection = []

    col_access = client.cota_access_test["sco_" + todayDate + "_" +str(int(timestamp))]
    
    count = 0
    for eachVisitedSet in output:
        accessibleStops = eachVisitedSet
        for eachStopIndex, eachStopValue in accessibleStops.items():
            # print(eachStopValue)
            if eachStopValue["lastTripType"] != None:
                recordCollection.append(eachStopValue)
                count += 1
    col_access.insert_many(recordCollection)
    # print("-----", todayDate, "-----", int(timestamp), "-----", count)
    col_access.create_index([("startStopID", 1)])

if __name__ == "__main__":
    basicSolver = BasicSolver.BasicSolver()
    # startDate = date(2019, 6, 20)
    startDate = date(2019, 7, 10)
    endDate = date(2019, 7, 11)
    # endDate = date(2019, 7, 2)
    walkingDistanceLimit = 700
    timeDeltaLimit = 120 * 60
    walkingSpeed = 1.4
    scooterSpeed = 4.47  # 10 mph
    scooterDistanceLimit = (5-1)/0.32*4.47*60 # 5 dollar 
    sampleRate = 50
    isRealTime = True
    isScooter = True
    daterange = (basicSolver.daterange(startDate, endDate))
    numberOfTimeSamples = 1 # how many samples you want to calculate per hour. If the value = 1, then every 1 hour. If 4, then every 15 minutes.
        
    for singleDate in (daterange):
        GTFSTimestamp = basicSolver.find_gtfs_time_stamp(singleDate)
        todaySeconds = atime.mktime(singleDate.timetuple())
        gtfsSeconds = str(transfer_tools.find_gtfs_time_stamp(singleDate))
        
        # Full stops
        db_GTFS = client.cota_gtfs
        col_stop = db_GTFS[gtfsSeconds + '_stops']
        rl_stop = list(col_stop.find({}))
        sampledStopsList = []
        for i in rl_stop:
            sampledStopsList.append(i["stop_id"])

        todayTimestampList = []
        for i in [8]:
            todayTimestampList.append(todaySeconds + i* 60*60/numberOfTimeSamples)
        
        for eachTimestamp in todayTimestampList:
            print("******************", singleDate, eachTimestamp, "******************")
            # if eachTimestamp != 1563192000: # debug and restart
            #     continue
            args = [int(eachTimestamp), walkingDistanceLimit, timeDeltaLimit, walkingSpeed, scooterSpeed, scooterDistanceLimit, isRealTime, isScooter]
            # resultsFeedback = collectiveAccessibilitySolve(args, sampledStopsList)
            # resultsFeedback = collectiveAccessibilitySolve(args, ["3RDMAIS"]) # Test

            testStopID = "3RDCAMW"
            resultsFeedback = singleAccessibilitySolve(args, testStopID)
            
            # print("eachTimestamp:", int(eachTimestamp), "results lens: ", len(resultsFeedback))
            print("******************", singleDate, eachTimestamp, "******************")
            # break
        # break
            