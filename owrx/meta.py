import json
import logging
import threading
import pickle
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from urllib import request
from urllib.error import HTTPError

from csdr.module import PickleModule
from owrx.aprs import AprsParser, AprsLocation
from owrx.config import Config
from owrx.map import Map, LatLngLocation
from owrx.bands import Bandplan

logger = logging.getLogger(__name__)


class Enricher(ABC):
    def __init__(self, parser):
        self.parser = parser

    @abstractmethod
    def enrich(self, meta, callback):
        pass


class RadioIDCache(object):
    sharedInstance = None

    @staticmethod
    def getSharedInstance():
        if RadioIDCache.sharedInstance is None:
            RadioIDCache.sharedInstance = RadioIDCache()
        return RadioIDCache.sharedInstance

    def __init__(self):
        self.cache = {}
        self.cacheTimeout = timedelta(seconds=86400)

    def isValid(self, mode, radio_id):
        key = self.__key(mode, radio_id)
        if key not in self.cache:
            return False
        entry = self.cache[key]
        return entry["timestamp"] + self.cacheTimeout > datetime.now()

    def __key(self, mode, radio_id):
        return "{}-{}".format(mode, radio_id)

    def put(self, mode, radio_id, value):
        self.cache[self.__key(mode, radio_id)] = {"timestamp": datetime.now(), "data": value}

    def get(self, mode, radio_id):
        if not self.isValid(mode, radio_id):
            return None
        return self.cache[self.__key(mode, radio_id)]["data"]


class RadioIDEnricher(Enricher):
    def __init__(self, mode, parser):
        super().__init__(parser)
        self.mode = mode
        self.threads = {}
        self.callbacks = {}

    def _fillCache(self, id):
        data = self._downloadRadioIdData(id)
        RadioIDCache.getSharedInstance().put(self.mode, id, data)
        if id in self.callbacks:
            while self.callbacks[id]:
                self.callbacks[id].pop()(data)
            del self.callbacks[id]
        del self.threads[id]

    def _downloadRadioIdData(self, id):
        try:
            logger.debug("requesting radioid metadata for mode=%s and id=%s", self.mode, id)
            res = request.urlopen("https://www.radioid.net/api/{0}/user/?id={1}".format(self.mode, id), timeout=30)
            if res.status != 200:
                logger.warning("radioid API returned error %i for mode=%s and id=%s", res.status, self.mode, id)
                return None
            data = json.loads(res.read().decode("utf-8"))
            if "count" in data and data["count"] > 0 and "results" in data:
                for item in data["results"]:
                    if "id" in item and item["id"] == id:
                        return item
        except json.JSONDecodeError:
            logger.warning("unable to parse radioid response JSON")
        except HTTPError as e:
            logger.warning("radioid responded with error: %s", str(e))

        return None

    def enrich(self, meta, callback):
        config_key = "digital_voice_{}_id_lookup".format(self.mode)
        if not Config.get()[config_key]:
            return meta
        if "source" not in meta:
            return meta
        id = int(meta["source"])
        cache = RadioIDCache.getSharedInstance()
        if not cache.isValid(self.mode, id):
            if id not in self.threads:
                self.threads[id] = threading.Thread(target=self._fillCache, args=[id], daemon=True)
                self.threads[id].start()
            if id not in self.callbacks:
                self.callbacks[id] = []

            def onFinish(data):
                if data is not None:
                    meta["additional"] = data
                callback(meta)

            self.callbacks[id].append(onFinish)
            return meta
        data = cache.get(self.mode, id)
        if data is not None:
            meta["additional"] = data
        return meta


class YsfMetaEnricher(Enricher):
    def enrich(self, meta, callback):
        for key in ["source", "up", "down", "target"]:
            if key in meta:
                meta[key] = meta[key].strip()
        for key in ["lat", "lon"]:
            if key in meta:
                meta[key] = float(meta[key])
        if "source" in meta and "lat" in meta and "lon" in meta:
            loc = LatLngLocation(meta["lat"], meta["lon"])
            Map.getSharedInstance().updateLocation(meta["source"], loc, "YSF", self.parser.getBand())
        return meta


class DStarEnricher(Enricher):
    def enrich(self, meta, callback):
        for key in ["lat", "lon"]:
            if key in meta:
                meta[key] = float(meta[key])
        if "ourcall" in meta and "lat" in meta and "lon" in meta:
            loc = LatLngLocation(meta["lat"], meta["lon"])
            Map.getSharedInstance().updateLocation(meta["ourcall"], loc, "D-Star", self.parser.getBand())
        if "dprs" in meta:
            try:
                # we can send the DPRS stuff through our APRS parser to extract the information
                # TODO: only third-party parsing accepts this format right now
                parser = AprsParser()
                dprsData = parser.parseThirdpartyAprsData(meta["dprs"])
                if "data" in dprsData:
                    data = dprsData["data"]
                    if "lat" in data and "lon" in data:
                        # TODO: we could actually get the symbols from the parsed APRS data and show that on the meta panel
                        meta["lat"] = data["lat"]
                        meta["lon"] = data["lon"]

                        if "ourcall" in meta:
                            # send location info to map as well (it will show up with the correct symbol there!)
                            loc = AprsLocation(data)
                            Map.getSharedInstance().updateLocation(meta["ourcall"], loc, "DPRS", self.parser.getBand())
            except Exception:
                logger.exception("Error while parsing DPRS data")

        return meta


class MetaParser(PickleModule):
    def __init__(self):
        self.enrichers = {
            "DMR": RadioIDEnricher("dmr", self),
            "YSF": YsfMetaEnricher(self),
            "DSTAR": DStarEnricher(self),
            "NXDN": RadioIDEnricher("nxdn", self),
        }
        self.currentMetaData = None
        self.band = None
        super().__init__()

    def process(self, meta):
        self.currentMetaData = None
        if "protocol" in meta:
            protocol = meta["protocol"]
            if protocol in self.enrichers:
                self.currentMetaData = meta = self.enrichers[protocol].enrich(meta, self.receive)
        return meta

    def receive(self, meta):
        # we may have moved on in the meantime
        if meta is not self.currentMetaData:
            return
        self.writer.write(pickle.dumps(meta))

    def setDialFrequency(self, freq):
        self.band = Bandplan.getSharedInstance().findBand(freq)

    def getBand(self):
        return self.band
