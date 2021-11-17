# Solve travel-time-based time-dependent routing problem based on GTFS trip update data with a Dijkstra approach. 
# The result is a record-based OD matrix with scheduled-based results (with SC suffix, SChedule) and real-time-based results (with RT suffix, Real-Time).
# The results (for both versions) include: 
# 1) origin and destination stop with stop_id;
# 2) travel time between the origin and destination, including total travel time, bus time, walk time, wait time; 
# 3) last stop, according to Dijkstra algorithm's backward structure;
# 4) last trip, including trip type ("bus" or "walk"), trip_id (if trip type is bus), transfer count (which is not right for now).

# The results are retrospective OD records generated by optimal algorithm. The locations are only bus stops, not including other places. 

import BasicSolver
import sys
import os
import time, math
import multiprocessing
import copy
from pymongo import MongoClient
from datetime import timedelta, date, datetime
from math import sin, cos, sqrt, atan2, pi, acos
from tqdm.notebook import tqdm
from collections import OrderedDict
import time as atime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transfer_tools
from ipyleaflet import Map, basemaps, Circle
client = MongoClient('mongodb://localhost:27017/')


class DijkstraSolver(BasicSolver.BasicSolver):
    def __init__(self, **kwarg):
        BasicSolver.BasicSolver.__init__(self)
        # timestamp = args[0] # The start timestamp when the hypothesis user leaves the start point, e.g. 8am, 12pm, 6pm
        # walkingDistanceLimit = args[1] # Walking distance limit, usually 700 meters
        # timeDeltaLimit = args[2] # A hard limit of stop time limit. The buses after that time limit will not be considered as a possible trip, even if it exists. Uusally 3 hours = 180 * 60 seconds
        # walkingSpeed = args[3] # Usually 1.4 meters per second
        # isRealTime = args[6] # Is the calcuation retrospective real-time based
        self.walkingDistanceLimit = kwarg["walkingDistanceLimit"]
        self.timestamp = kwarg["timestamp"]
        self.timeDeltaLimit = kwarg["timeDeltaLimit"]
        self.walkingSpeed = kwarg["walkingSpeed"]
        self.isRealTime = kwarg["isRealTime"]
        self.walkingTimeLimit = self.walkingDistanceLimit/self.walkingSpeed

        # Time
        self.curDateTime = datetime.fromtimestamp(self.timestamp)
        self.curDate = self.curDateTime.date()
        todayDate = self.curDate.strftime("%Y%m%d")
        self.todaySeconds = time.mktime(time.strptime(todayDate, "%Y%m%d"))
        self.timeLimit = min(self.todaySeconds + 86400, self.timestamp + self.timeDeltaLimit) # Limit the time to just one day. 
        self.todayDate = todayDate
        self.curGTFSTimestamp = self.find_gtfs_time_stamp(self.curDate)

        # print("current GTFS timestamp", self.curGTFSTimestamp)

        # Mongo GTFS setup
        self.db_GTFS = client.cota_gtfs # GTFS static schedule database
        self.db_real_time = client.cota_real_time # GTFS real-time database
        self.col_stops = self.db_GTFS[str(self.curGTFSTimestamp) + "_stops"]
        self.col_stop_times = self.db_GTFS[str(
            self.curGTFSTimestamp) + "_stop_times"]
        self.col_real_times = self.db_real_time["R" + todayDate]
        self.db_access = client.cota_access_rel

        self.rl_stops = list(self.col_stops.find({}))
        self.rl_stop_times_rt = list(self.col_real_times.find({"time": {"$gt": self.timestamp, "$lt": self.timeLimit}})) # Real-time
        self.rl_stop_times_sc = list(self.col_real_times.find({"scheduled_time": {"$gt": self.timestamp, "$lt": self.timeLimit}})) # Scheduled
        
        # print(len(self.rl_stop_times), timestamp, self.timeLimit)

        if self.rl_stop_times_rt != None:
            self.rl_stop_times_rt.sort(key=self.sortrlStopTimesRealtime)
            self.rl_stop_times_sc.sort(key=self.sortrlStopTimesSchedule)

        self.stopsDic = {}
        self.visitedSet = {}
        self.timeListByStopRT = {}
        self.timeListByTripRT = {}
        self.timeListByStopSC = {}
        self.timeListByTripSC = {}
        self.arcsDicRT = {}
        self.arcsDicSC = {}

        for eachStop in self.rl_stops:
            self.stopsDic[eachStop["stop_id"]] = eachStop

        for eachTime in self.rl_stop_times_rt:
            stopID = eachTime["stop_id"]
            tripID = eachTime["trip_id"]
            try:
                self.timeListByStopRT[stopID]
            except:
                self.timeListByStopRT[stopID] = []

            self.timeListByStopRT[stopID].append(eachTime)

            try:
                self.timeListByTripRT[tripID]
            except:
                self.timeListByTripRT[tripID] = []

            self.timeListByTripRT[tripID].append(eachTime)
        
        for eachTime in self.rl_stop_times_sc:
            stopID = eachTime["stop_id"]
            tripID = eachTime["trip_id"]
            try:
                self.timeListByStopSC[stopID]
            except:
                self.timeListByStopSC[stopID] = []

            self.timeListByStopSC[stopID].append(eachTime)

            try:
                self.timeListByTripSC[tripID]
            except:
                self.timeListByTripSC[tripID] = []

            self.timeListByTripSC[tripID].append(eachTime)
        

        for eachTripID, eachTrip in self.timeListByTripRT.items():
            if len(eachTrip) < 2:
                continue
            for index in range(0, len(eachTrip)-1):
                generatingStopID = eachTrip[index]["stop_id"]
                receivingStopID = eachTrip[index + 1]["stop_id"]
                try:
                    self.arcsDicRT[generatingStopID]
                except:
                    self.arcsDicRT[generatingStopID] = {}

                try:
                    self.arcsDicRT[generatingStopID][receivingStopID]
                except:
                    self.arcsDicRT[generatingStopID][receivingStopID] = {}

                timeGen = eachTrip[index]["time"]
                timeRec = eachTrip[index + 1]["time"]

                try:
                    self.arcsDicRT[generatingStopID][receivingStopID][timeGen]
                except:
                    self.arcsDicRT[generatingStopID][receivingStopID][timeGen] = {
                        'time_gen': timeGen, 'time_rec': timeRec, 'bus_time': - timeGen + timeRec, "trip_id": eachTripID}

        for eachTripID, eachTrip in self.timeListByTripSC.items():
            if len(eachTrip) < 2:
                continue
            for index in range(0, len(eachTrip)-1):
                generatingStopID = eachTrip[index]["stop_id"]
                receivingStopID = eachTrip[index + 1]["stop_id"]
                try:
                    self.arcsDicSC[generatingStopID]
                except:
                    self.arcsDicSC[generatingStopID] = {}

                try:
                    self.arcsDicSC[generatingStopID][receivingStopID]
                except:
                    self.arcsDicSC[generatingStopID][receivingStopID] = {}

                timeGen = eachTrip[index]["scheduled_time"]
                timeRec = eachTrip[index + 1]["scheduled_time"]

                try: 
                    self.arcsDicSC[generatingStopID][receivingStopID][timeGen]
                except:
                    self.arcsDicSC[generatingStopID][receivingStopID][timeGen] = {
                        'time_gen': timeGen, 'time_rec': timeRec, 'bus_time': - timeGen + timeRec, "trip_id": eachTripID}

        # Sort the arcsDics with OrderedDic.
        def return_time_gen(e):
            # return e["time_gen"]
            return e[0]

        for startStopID, startStop in self.arcsDicRT.items():
            for endStopID, endStop in startStop.items():
                self.arcsDicRT[startStopID][endStopID] = OrderedDict(sorted(endStop.items(), key=return_time_gen))

        for startStopID, startStop in self.arcsDicSC.items():
            for endStopID, endStop in startStop.items():
                self.arcsDicSC[startStopID][endStopID] = OrderedDict(sorted(endStop.items(), key=return_time_gen))
        
        self.count = 0
        # print("Initialization: Done!")

    def sortrlStopTimesSchedule(self, value):
        return value["scheduled_time"]

    def sortrlStopTimesRealtime(self, value):
        return value["time"]

    def calculateDistance(self, latlng1, latlng2):
        R = 6373
        lat1 = float(latlng1["stop_lat"])
        lon1 = float(latlng1["stop_lon"])
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

    def findClosestStop(self, isRT): # Find the geographically closest stop.
        minDistance = sys.maxsize
        closestStop = False
        for eachStop in self.rl_stops:
            eachStopID = eachStop["stop_id"]
            # print(minDistance, self.visitedSet[eachStopID]['time'])
            # if self.visitedSet[eachStopID]['time'] < minDistance:
            #     print(eachStopID, self.visitedSet[eachStopID]['visitTag'])
            if isRT:
                if self.visitedSet[eachStopID]['timeRT'] < minDistance and self.visitedSet[eachStopID]['visitTagRT'] == False:
                    minDistance = self.visitedSet[eachStopID]['timeRT']
                    closestStop = eachStop
            else:
                if self.visitedSet[eachStopID]['timeSC'] < minDistance and self.visitedSet[eachStopID]['visitTagSC'] == False:
                    minDistance = self.visitedSet[eachStopID]['timeSC']
                    closestStop = eachStop

        # print(closestStop)
        return closestStop

    def findClosestStopFromCoordinates(self, coordinates):
        minDistance = sys.maxsize
        closestStop = False
        for eachStop in self.rl_stops:
            eachStopID = eachStop["stop_id"]
            thisDistance = self.calculateDistance(coordinates, eachStop)
            if thisDistance < minDistance:
                minDistance = thisDistance
                closestStop = eachStopID
        if minDistance > 10000:
            closestStop = False
        return closestStop

    def getTravelTimeRT(self, closestStopID, eachStopID, aStartTime): # Get the travel time (network cost) between the two stops given the arrival time at the start stop
        # With bus trip, can wait and bus or walk
        # All methods are exclusive between two stops. Options:
        # 1. bus + wait. For all arcs.
        # 2. walk. 
        travelTime = {
            "generatingStopIDRT": closestStopID,
            "receivingStopIDRT": eachStopID,
            "timeRT": sys.maxsize,
            "walkTimeRT": None,
            "busTimeRT": None,
            "waitTimeRT": None,
            "tripIDRT": None,
            "tripTypeRT": None
        }

        closestStop = self.stopsDic[closestStopID]
        eachStop = self.stopsDic[eachStopID]

        # 1. Propagate with bus and wait. Not controlling transfer count. Select the most recent trip.
        # More greedy: if there is a bus trip, wait for that regardless of the expected saved time by walking.
    
        try:
            self.arcsDicRT[closestStopID][eachStopID]
        except:
            pass # Not bus trip
        else:
            thisArcsDic = self.arcsDicRT[closestStopID][eachStopID]
            for eachIndex, eachTrip in thisArcsDic.items():
                # if closestStopID == "NORBARNW":
                #     a = 1
                if eachTrip["time_gen"] >= aStartTime: # This requires that arcDic[Start][End] to be an ascending sequence. So use OrderedDic to maintain this feature.
                    recentBusTime = eachTrip["time_gen"]
                    travelTime["tripIDRT"] = eachTrip["trip_id"]
                    travelTime["busTimeRT"] = eachTrip["bus_time"]
                    travelTime["waitTimeRT"] = recentBusTime - aStartTime
                    travelTime["walkTimeRT"] = 0
                    travelTime["timeRT"] = travelTime["busTimeRT"] + travelTime["waitTimeRT"]
                    travelTime["tripTypeRT"] = "bus"
                    break
            
            # if walkTime_walk > travelTime["timeRT"]:
            return travelTime
            
        # 2. Propagate with walk
        if self.visitedSet[closestStopID]["lastTripTypeRT"] == "walk": # cannot make two subsequent non-transit arcs.
            return travelTime
        
        dist = self.calculateDistance(self.stopsDic[closestStopID], self.stopsDic[eachStopID]) # Can be precalculated
        walkTime_walk = dist/self.walkingSpeed
        totalTime_walk = walkTime_walk

        if totalTime_walk > self.walkingTimeLimit:
            pass
        elif travelTime["timeRT"] > totalTime_walk: # if no bus trip, travelTime["time"] should be maxsize; or, the bus trip is very slow, but very unlikely
            travelTime["busTimeRT"] = 0
            travelTime["waitTimeRT"] = 0
            travelTime["walkTimeRT"] = walkTime_walk
            travelTime["timeRT"] = totalTime_walk
            travelTime["tripTypeRT"] = "walk"
            travelTime["tripIDRT"] = None

        return travelTime


    def getTravelTimeSC(self, closestStopID, eachStopID, aStartTime): 
        # With bus trip, can wait and bus or walk
        # All methods are exclusive between two stops. Options:
        # 1. bus + wait. For all arcs.
        # 2. walk. 

        travelTime = {
            "generatingStopIDSC": closestStopID,
            "receivingStopIDSC": eachStopID,
            "timeSC": sys.maxsize,
            "walkTimeSC": None,
            "busTimeSC": None,
            "waitTimeSC": None,
            "tripIDSC": None,
            "tripTypeSC": None
        }

        closestStop = self.stopsDic[closestStopID]
        eachStop = self.stopsDic[eachStopID]

        # 1. Propagate with bus and wait. Not controlling transfer count. Select the most recent trip.
        # More greedy: if there is a bus trip, wait for that regardless of the expected saved time by walking.
        try:
            self.arcsDicSC[closestStopID][eachStopID]
        except:
            pass # Not bus trip
        else:
            for eachIndex, eachTrip in self.arcsDicSC[closestStopID][eachStopID].items():
                if eachTrip["time_gen"] >= aStartTime:
                    recentBusTime = eachTrip["time_gen"]
                    travelTime["tripIDSC"] = eachTrip["trip_id"]
                    travelTime["busTimeSC"] = eachTrip["bus_time"]
                    travelTime["waitTimeSC"] = recentBusTime - aStartTime
                    travelTime["walkTimeSC"] = 0
                    travelTime["timeSC"] = travelTime["busTimeSC"] + travelTime["waitTimeSC"]
                    travelTime["tripTypeSC"] = "bus"
                    break
            return travelTime
            
        # 2. Propagate with walk
        if self.visitedSet[closestStopID]["lastTripTypeSC"] == "walk": # cannot make two subsequent non-transit arcs.
            return travelTime
        dist = self.calculateDistance(self.stopsDic[closestStopID], self.stopsDic[eachStopID]) # Can be precalculated
        walkTime_walk = dist/self.walkingSpeed
        totalTime_walk = walkTime_walk

        if totalTime_walk > self.walkingTimeLimit:
            pass
        elif travelTime["timeSC"] > totalTime_walk: # if no bus trip, travelTime["time"] should be maxsize; or, the bus trip is very slow, but very unlikely
            travelTime["busTimeSC"] = 0
            travelTime["waitTimeSC"] = 0
            travelTime["walkTimeSC"] = walkTime_walk
            travelTime["timeSC"] = totalTime_walk
            travelTime["tripTypeSC"] = "walk"

        return travelTime


    def addToTravelTime(self, originalStrucuture, closestStructure, travelTime, tempStartTimestamp, isRT=True):
        if isRT:
            originalStrucuture["timeRT"] = closestStructure["timeRT"] + travelTime["timeRT"]
            originalStrucuture["walkTimeRT"] = closestStructure["walkTimeRT"] + travelTime["walkTimeRT"]
            originalStrucuture["busTimeRT"] = closestStructure["busTimeRT"] + travelTime["busTimeRT"]
            originalStrucuture["waitTimeRT"] = closestStructure["waitTimeRT"] + travelTime["waitTimeRT"]
            
            if travelTime["tripTypeRT"] == "bus" and travelTime["tripIDRT"] != originalStrucuture["lastTripIDRT"]:
                originalStrucuture["transferCountRT"] += 1

            originalStrucuture["lastTripTypeRT"] = travelTime["tripTypeRT"]
            originalStrucuture["lastTripIDRT"] = travelTime["tripIDRT"]
            originalStrucuture["generatingStopIDRT"] = travelTime["generatingStopIDRT"]
        else:
            originalStrucuture["timeSC"] = closestStructure["timeSC"] + travelTime["timeSC"]
            originalStrucuture["walkTimeSC"] = closestStructure["walkTimeSC"] + travelTime["walkTimeSC"]
            originalStrucuture["busTimeSC"] = closestStructure["busTimeSC"] + travelTime["busTimeSC"]
            originalStrucuture["waitTimeSC"] = closestStructure["waitTimeSC"] + travelTime["waitTimeSC"]
            
            if travelTime["tripTypeSC"] == "bus" and travelTime["tripIDSC"] != originalStrucuture["lastTripIDSC"]:
                originalStrucuture["transferCountSC"] += 1

            originalStrucuture["lastTripTypeSC"] = travelTime["tripTypeSC"]
            originalStrucuture["lastTripIDSC"] = travelTime["tripIDSC"]
            originalStrucuture["generatingStopIDSC"] = travelTime["generatingStopIDSC"]

        return originalStrucuture


    # Dijkstraly find shortest time to each stop.
    def extendStops(self, startStopID): # Corresponded to dijjkstra.docx, Line 1
        self.aStartStopID = startStopID
        startTimestamp = self.timestamp 
        self.visitedSet = {} # Line 2
        for eachStop in self.rl_stops: # Initialization, Line 3
            eachStopID = eachStop["stop_id"]
            self.visitedSet[eachStopID] = { 
                "startStopID": startStopID, # Origin stop
                "receivingStopID": eachStopID, # Destination stop
                "timeRT": sys.maxsize, # Line 4
                "walkTimeRT": 0,
                "busTimeRT": 0,
                "waitTimeRT": 0,
                "generatingStopIDRT": None, # The last visited stop by Real-time, Line 5
                "lastTripIDRT": None,
                "lastTripTypeRT": None,
                "transferCountRT": 0,
                "visitTagRT": False,
                "timeSC": sys.maxsize, # Line 4
                "walkTimeSC": 0,
                "busTimeSC": 0,
                "waitTimeSC": 0,
                "generatingStopIDSC": None, # The last visited stop by Schedule, Line 5
                "lastTripIDSC": None,
                "lastTripTypeSC": None,
                "transferCountSC": 0,
                "visitTagSC": False,
                "stop_lat": self.stopsDic[eachStopID]["stop_lat"],
                "stop_lon": self.stopsDic[eachStopID]["stop_lon"]
            } # Line 6

        self.visitedSet[startStopID]["timeRT"] = 0 # initialization, Line 7
        self.visitedSet[startStopID]["timeSC"] = 0 # initialization, Line 7

        # OD and trips with RT timetable
        print("------ Retrospective Timetable Routing Running... ------")
        for _ in tqdm(self.rl_stops): # Line 8. Instead of using a while loop, I use a for loop to enumerate all the stops, which is equivilent except some redundants
            closestStop = self.findClosestStop(True) # Line 9: find the closest node in set Q to the S set, which is the ones having the confirmed clostest distance
            if closestStop == False: # Cannot find any closest nodes, every node is visited.
                # print("break!")
                break
            closestStopID = closestStop["stop_id"]

            self.visitedSet[closestStopID]["visitTagRT"] = True # Line 10: remove u from Q

            # Can modify self.rl_stops to improve performance.

            for eachStop in (self.rl_stops): # Line 11: For every neighbor of the closest stop to the set S, which is the ones having the confirmed closest distance.
                eachStopID = eachStop["stop_id"]
                if self.visitedSet[eachStopID]["visitTagRT"] == True: # Only for vertices that not in S.
                    continue
                
                tempStartTimestamp = startTimestamp + self.visitedSet[closestStopID]["timeRT"] # Line 12
                travelTime = self.getTravelTimeRT(closestStopID, eachStopID, tempStartTimestamp) # Find the weight between closestStop and eachStop
                # if travelTime["tripType"] != None:
                #     print(self.visitedSet[eachStopID]["time"] > self.visitedSet[closestStopID]["time"] + travelTime["time"])
                if travelTime["timeRT"] == None:
                    continue
                if self.visitedSet[eachStopID]["timeRT"] > self.visitedSet[closestStopID]["timeRT"] + travelTime["timeRT"]: # Line 13
                    self.visitedSet[eachStopID] = self.addToTravelTime(self.visitedSet[eachStopID], self.visitedSet[closestStopID], travelTime, tempStartTimestamp, True) # Line 14 -15
                    # print("changed: ", self.visitedSet[eachStopID])
            # print(_["stop_id"], "finished!")
            # break
        # print(self.visitedSet)
        print("------ Retrospective Timetable Routing Finished... ------")

        print("------ Scheduled Timetable Routing Running... ------")
        # OD and trips with scheduled timetable
        for _ in tqdm(self.rl_stops):
            closestStop = self.findClosestStop(False) # Dijkstra's algorithm: find the closest node to the S set, which is the ones having the confirmed clostest distance.
            if closestStop == False:
                # print("break!")
                break
            closestStopID = closestStop["stop_id"]

            self.visitedSet[closestStopID]["visitTagSC"] = True # Add closest stop to S set. 

            # Can modify self.rl_stops to improve performance.

            for eachStop in (self.rl_stops):
                eachStopID = eachStop["stop_id"]
                if self.visitedSet[eachStopID]["visitTagSC"] == True:
                    continue
                tempStartTimestamp = startTimestamp + self.visitedSet[closestStopID]["timeSC"]
                travelTime = self.getTravelTimeSC(closestStopID, eachStopID, tempStartTimestamp)
                # if travelTime["tripType"] != None:
                #     print(self.visitedSet[eachStopID]["time"] > self.visitedSet[closestStopID]["time"] + travelTime["time"])
                if travelTime["timeSC"] == None:
                    continue
                if self.visitedSet[eachStopID]["timeSC"] > self.visitedSet[closestStopID]["timeSC"] + travelTime["timeSC"]:
                    self.visitedSet[eachStopID] = self.addToTravelTime(self.visitedSet[eachStopID], self.visitedSet[closestStopID], travelTime, tempStartTimestamp, False)
                    
                    # if eachStopID == "NORKINS1":
                    #     print("changed: ", self.visitedSet[eachStopID])
            # print(_["stop_id"], "finished!")
            # break
        # print(self.visitedSet)
        print("------ Scheduled Timetable Routing Finished... ------")

        return self.visitedSet

    def visualize(self, results, timeBudget):
        m = Map(basemap=basemaps.OpenStreetMap.Mapnik, center=(39.963596, -83.000944), zoom=12)

        for receivingStopID, ODRecord in results.items():
            radius = min((timeBudget * 60 - ODRecord["timeSC"]) * self.walkingSpeed, self.walkingDistanceLimit) 
            circle = Circle()
            circle.location = (ODRecord["stop_lat"], ODRecord["stop_lon"])
            circle.radius = radius
            circle.color = "black"
            circle.fill_color = "green"

            m.add_layer(circle)
        m


def singleAccessibilitySolve(args, startLocation): # Calculate travel time from startLocation to all other stops.
    accessibilitySolver = DijkstraSolver(**args)
    if type(startLocation) is not str:
        startLocation = accessibilitySolver.findClosestStopFromCoordinates(coordinates=startLocation)
        if not startLocation:
            return False
    lenStops = accessibilitySolver.extendStops(startLocation)
    # for a, b in lenStops.items():
    #     print(a, b["timeRT"], b["timeSC"])
    return accessibilitySolver, lenStops

def collectiveAccessibilitySolve(args, sampledStopsList):
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
        sub_output = pool.starmap(singleAccessibilitySolve, zip([args]*len(sub_sampledStopsList), sub_sampledStopsList))
        pool.close()
        pool.join()
        collectiveInsert(args, sub_output)

def collectiveInsert(args, output):
    timestamp = args[0]
    
    curDateTime = datetime.fromtimestamp(timestamp)
    curDate = curDateTime.date()
    todayDate = curDate.strftime("%Y%m%d")
    recordCollection = []

    col_access = client.cota_access_test["add_" + todayDate + "_" +str(int(timestamp))]

    count = 0
    for eachVisitedSet in output:
        accessibleStops = eachVisitedSet
        for eachStopIndex, eachStopValue in accessibleStops.items():
            # print(eachStopValue)
            # if eachStopValue["lastTripTypeRT"] != None:
            recordCollection.append(eachStopValue)
            count += 1
    col_access.insert_many(recordCollection)
    # print("-----", todayDate, "-----", int(timestamp), "-----", count)
    col_access.create_index([("startStopID", 1)])

if __name__ == "__main__":
    basicSolver = BasicSolver.BasicSolver()
    startDate = date(2019, 7, 10)
    endDate = date(2019, 7, 11)
    walkingDistanceLimit = 700
    timeDeltaLimit = 180 * 60
    walkingSpeed = 1.4
    sampleRate = 20
    isRealTime = True
    daterange = (basicSolver.daterange(startDate, endDate))
    numberOfTimeSamples = 1 # how many samples you want to calculate per hour. If the value = 1, then every 1 hour. If 4, then every 15 minutes.
    
    
        
    for singleDate in (daterange):
        weekday = singleDate.weekday()
        # if weekday != 2:
        #     continue
        GTFSTimestamp = basicSolver.find_gtfs_time_stamp(singleDate)
        todaySeconds = atime.mktime(singleDate.timetuple())
        gtfsSeconds = str(transfer_tools.find_gtfs_time_stamp(singleDate))
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
            print("****************** Date: ", singleDate, "; Timestamp: ", int(eachTimestamp), " Start... ******************")
            args = {
                "timestamp": int(eachTimestamp), 
                "walkingDistanceLimit": walkingDistanceLimit, 
                "timeDeltaLimit": timeDeltaLimit, 
                "walkingSpeed": walkingSpeed, 
                "isRealTime": isRealTime, 
            }
            
            testStopID = "3RDCAMW"
            resultsFeedback = singleAccessibilitySolve(args, testStopID)
            print("****************** Date: ", singleDate, "; Timestamp: ", int(eachTimestamp), "Finished... ******************")
            