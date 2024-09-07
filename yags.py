import requests, datetime
import json

class Client:
  def __init__(self, serverUri):
    self.serverUri = serverUri

  def apiPost(self, addr, params = []):
    url = f"{self.serverUri}/api/{addr}"

    x = requests.post(url, data = params)

    return x.text

  def getReceiver(self, rxId):
    return Receiver(rxId, self)

  def getTransmitter(self, txId):
    return Transmitter(txId, self)

class Receiver:
  def __init__(self, selfId, client):
    self.rxId   = selfId
    self.client = client

  def getId(self):
    return self.rxId

  def planObservation(self, tx, start, end):
    # 2024-08-15T10:00
    utcStart = start.replace(tzinfo=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")
    utcEnd   = end  .replace(tzinfo=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")

    res = self.client.apiPost("observation/plan", {
      "receiver":    self.getId(),
      "transmitter": tx  .getId(),
      "start":       utcStart,
      "end":         utcEnd
    })

    res = json.loads(res)

    if (res["status"]):
      return Observation(res["id"], self.client)

    return False

class Transmitter:
  def __init__(self, selfId, client):
    self.txId   = selfId
    self.client = client

  def planUplink(self, tx, data, delay = None, start = None):
    if start is not None:
      utcStart = start.replace(tzinfo=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")
      delay    = 0
    elif delay is not None:
      utcStart = "2106-02-07T06:28:15"
    else:
      return False

    res = self.client.apiPost("uplink/plan", {
      "transmitter": tx  .getId(),
      "receiver":    self.getId(),
      "start":       utcStart,
      "delay":       delay,
      "data":        data
    })

    res = json.loads(res)

    if (res["status"]):
      return Uplink(res["id"], self.client)

    return False

  def getId(self):
    return self.txId

class Uplink:
  def __init__(self, selfId, client):
    self.upID   = selfId
    self.client = client

  def status(self):
    res = self.client.apiPost("uplink/status", {
      "id": self.getId(),
    })

    res = json.loads(res)

    if (res["status"]):
      return res["value"]

  def isDone(self):
    return self.status() == "done"

  def getId(self):
    return self.upID

class Observation:
  def __init__(self, selfId, client):
    self.obId   = selfId
    self.client = client

  def getId(self):
    return self.obId

  def getPackets(self):
    url = f"{self.client.serverUri}/ARTEFACTS/{self.getId()}/packets.json"

    x = requests.get(url)

    try:
      return json.loads(x.text)
    except:
      return []
