from geolocation_engine import *
import numpy as np

def rssiToDst(rssi):
	return 1/rssi

def locate(uplink):
	uplinks = list()

	for rx in uplink["uplink_message"]["rx_metadata"]:
		if not "location" in rx:
			continue

		uplinks.append(
			Uplink(
				time     = np.datetime64(rx["timestamp"]).astype('datetime64[ns]').astype('int').item(),
				rssi     = rx["rssi"],
				snr      = rx["snr"],
				bstn_eui = rx["gateway_ids"]["eui"],
				bstn_lat = rx["location"]["latitude"],
				bstn_lng = rx["location"]["longitude"]
			)
		)

	if   len(uplinks) == 0:
		return 0, 0, 0
	elif len(uplinks) == 1: # simply use GS position as sat position
		return uplinks[0]._bstn_lat, uplinks[0]._bstn_lng, 1
	elif len(uplinks) == 2: # use weighed mean as sat position
		return (uplinks[0]._bstn_lat * abs(1/uplinks[0]._rssi) + uplinks[1]._bstn_lat * abs(1/uplinks[1]._rssi)) / (abs(1/uplinks[0]._rssi) + abs(1/uplinks[1]._rssi)), (uplinks[0]._bstn_lng * abs(1/uplinks[0]._rssi) + uplinks[1]._bstn_lng * abs(1/uplinks[1]._rssi)) / (abs(1/uplinks[0]._rssi) + abs(1/uplinks[1]._rssi)), 2

	# else use triangulation with RSSI

	tx = Transaction(dev_eui='', join_id=0, seq_no=0, datarate=0, uplinks=uplinks)
	location_engine = LocationEngine(transaction=tx, debug=False)

	lat, lon = location_engine.compute_device_location()

	return lat, lon, len(uplinks) # lat and lon