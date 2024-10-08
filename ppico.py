import yags, datetime, time
import os
import sys, select
import subprocess
import paho.mqtt.client as mqtt
import geolocation
import json
import base64
import math
import argparse

RX_WINDOW_DELAY        = 66 # 66 sec after TX
                            # 6sec as tolerace 8s window
                            # RX WINDOW2 60s after TX         

OB_LENGTH              = 60 # 60 mins
DEV_ADDR               = ""
BASELINE               = 1013.25

# PPICO CONSTANT define
PPICO_EE_VALIDITY      = 0
PPICO_EE_RESET_COUNTER = 1
PPICO_EE_TX_COUNTER    = 2
PPICO_EE_RX_COUNTER    = 4
PPICO_EE_FREQ_TABLE    = 6
PPICO_EE_DR_TABLE      = 30
PPICO_EE_DELAY         = 48
PPICO_EE_CH1_INDEX     = 49
PPICO_EE_CH2_INDEX     = 50
PPICO_EE_RESERVE       = 51
PPICO_EE_HISTORY       = 128

parser = argparse.ArgumentParser(description='Ground station client for PPICO V1 mission')

parser.add_argument('--yags',      type=str, help='HOST of yags server with port')
parser.add_argument('--yags_tx',   type=str, help='UUID of satellite transmitter on yags server')
parser.add_argument('--yags_rx',   type=str, help='UUID of groundstation on yags server')
parser.add_argument('--lw_appkey', type=str, help='Appkey for decode raw LW packets')
parser.add_argument('--lw_dev',    type=str, help='Device address for decode raw LW packets')

parser.add_argument('--ttn_app',  type=str, help='ID of TTN application')
parser.add_argument('--ttn_dev',  type=str, help='ID of TTN end device')
parser.add_argument('--ttn_api',  type=str, help='API key for TTN')
parser.add_argument('--ttn_host', type=str, help='TTN host address')

args       = parser.parse_args()

sat        = None
station    = None
yagsServer = None
ob         = None

if args.yags is None and args.ttn_host is None:
    print("No yags or ttn_host definated. One of it is needed")
    exit(1)

if args.yags is not None:
    if args.yags_tx is None or args.yags_rx is None or args.lw_appkey is None or args.lw_dev is None:
        print("No definaded yags_tx or yags_rx or lw_appkey or lw_dev")
        exit(1)

    yagsServer  = yags.Client              (args.yags)
    sat         = yagsServer.getTransmitter(args.yags_tx)
    station     = yagsServer.getReceiver   (args.yags_rx)

    DEV_ADDR = args.lw_dev 

    ob = station.planObservation(
        sat,
        datetime.datetime.now(datetime.timezone.utc),
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=OB_LENGTH)
    )

if args.ttn_host is not None:
    if (args.ttn_dev is None or args.ttn_api is None or args.ttn_app is None):
        print("No definaded ttn_app or ttn_dev ot ttn_api")
        exit(1)

lastPacketTime    = 0
lastRawPacketTime = 0
lastPacketSnr     = 0
lastFError        = 0
lastPacketRSSI    = 0
packetsCount      = 0
upCounter         = 0
lastUplinkTime    = 0
TTNForDownLink    = False


lastPackets    = []
planedUplinks  = []
lastLWPackets  = []
lastTelemetry  = []

status         = "READY"

def toHex(val, size):
    return f'%0{size}x' % val

def twos_complement(hexstr, bits):
    value = int(hexstr, 16)
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value

def EEWRITE(addr, byte):
    return f"91{toHex(addr, 4)}{toHex(byte, 2)}"

def EEREAD(addr, size, raw = False, rawFRP = 0, rawDRP = 5 * 3):
    size = size & 0b00111111

    if raw:
        size |= 0b10000000

    return f"90{toHex(addr, 4)}{toHex(size, 2)}{toHex(rawFRP, 2)}{toHex(rawDRP, 2)}"

def EECPY(source, addr, label = ""):
    global planedUplinks

    curretCmd      = ""
    curretExpected = ""
    baseAddr       = addr

    for byte in source:
        tmpCmd = EEWRITE(addr, byte)

        pktMaxSize = 30 # 15B 30 hex nums

        if TTNForDownLink:
            pktMaxSize = 100 # packets can be bigger beacuse we in data part 50B 100 hexnums

        # 6 bytes size of readout cmd 12 hex nums
        if (len(curretCmd) + len(tmpCmd) + 12 > pktMaxSize):
            planedUplinks.append({
                "data":     curretCmd + EEREAD(baseAddr, addr - baseAddr, False),
                "expected": curretExpected, # readout check
                "status":   label + " CPY " + str(baseAddr) + " to " + str(addr - 1),
                "send":     True
            })

            curretCmd      = ""
            curretExpected = ""
            baseAddr       = addr

        curretCmd      += tmpCmd
        curretExpected += toHex(byte, 2)

        addr += 1

    if (len(curretCmd) > 0):
        planedUplinks.append({
            "data":     curretCmd + EEREAD(baseAddr, addr - baseAddr, False),
            "expected": curretExpected, # readout check
            "status":   label + " CPY " + str(baseAddr) + " to " + str(addr - 1),
            "send":     True
        })

        



def setupEEPROM():
    global planedUplinks

    os.system('cls' if os.name == 'nt' else 'clear')

    print('Are you sure. Do you realy want SETUP EEPROM (yes/NO):')
    x = input()

    if x != "yes":
        return

    # set delay to 0 (16s)
    EECPY([0x00], PPICO_EE_DELAY, label = "CHANGE DELAY TO 16s")

    # write freqvency table
    EECPY([
        #0xD9, 0x06, 0x8B,  # Channel 0 868.100 MHz / 61.035 Hz = 14222987 = 0xD9068B alredy in eeprom by default
        0xD9, 0x13, 0x58,  # Channel 1 868.300 MHz / 61.035 Hz = 14226264 = 0xD91358
        0xD9, 0x20, 0x24,  # Channel 2 868.500 MHz / 61.035 Hz = 14229540 = 0xD92024
        0xD8, 0xC6, 0x8B,  # Channel 3 867.100 MHz / 61.035 Hz = 14206603 = 0xD8C68B
        0xD8, 0xD3, 0x58,  # Channel 4 867.300 MHz / 61.035 Hz = 14209880 = 0xD8D358
        0xD8, 0xE0, 0x24,  # Channel 5 867.500 MHz / 61.035 Hz = 14213156 = 0xD8E024
        0xD8, 0xEC, 0xF1,  # Channel 6 867.700 MHz / 61.035 Hz = 14216433 = 0xD8ECF1
        0xD8, 0xF9, 0xBE   # Channel 7 867.900 MHz / 61.035 Hz = 14219710 = 0xD8F9BE
    ], PPICO_EE_FREQ_TABLE + 3, label = "FREQ TABLE")

    # Set indexes of datarate to last in table SF12
    EECPY([0x50, 0x50], PPICO_EE_CH1_INDEX, label = "SF12 BW128")

    # write data rate table
    EECPY([
        0x74, 0x72, 0x04,  # SF7BW125 - 0
        0x84, 0x72, 0x04,  # SF8BW125 - 1
        0x94, 0x72, 0x04,  # SF9BW125 - 2
        0xA4, 0x72, 0x04,  # SF10BW125 - 3
        0xB4, 0x72, 0x0C,  # SF11BW125 - 4
        #0xC4, 0x72, 0x0C  # SF12BW125 alredy in eeprom by default - 5
    ], PPICO_EE_DR_TABLE, label = "DATARATE TABLE")

    # write some nice test data to reserve region :)
    # this region is reserved for retransmit from sat
    # Frist byte is size of data and others is PPICO V1 HELLO
    #EECPY([14, 0x50, 0x50, 0x49, 0x43, 0x4F, 0x20, 0x56, 0x31, 0x20, 0x48, 0x45, 0x4C, 0x4C, 0x4F], PPICO_EE_RESERVE, label = "RESERVED DATA")

    # reset all counters
    # RST counter 1B TX 2B RX 2B
    EECPY([0],    PPICO_EE_RESET_COUNTER, label = "RESET RST COUNTER")
    EECPY([0, 0], PPICO_EE_RX_COUNTER,    label = "RESET RX  COUNTER")
    EECPY([0, 0], PPICO_EE_TX_COUNTER,    label = "RESET TX  COUNTER")

    # set FR index 2 to random SF 12
    EECPY([0x50, 0x5F], PPICO_EE_CH1_INDEX, label = "RANDOM FREQ")

    # set delay back (~5m)
    EECPY([0x03], PPICO_EE_DELAY, label = "CHANGE DELAY TO 5m")

def UserEECPY():
    os.system('cls' if os.name == 'nt' else 'clear')

    print('Start addres in eeprom:')

    pos = int(input())

    print('Data to write (HEX):')

    data = input()

    dataBytes = []
    for i in range(0, len(data), 2):
        dataBytes.append(int(data[i : i + 2], 16))

    EECPY(dataBytes, pos, label = "USER EECPY")



def advanced():
    os.system('cls' if os.name == 'nt' else 'clear')

    print('C  - Copy data to eeprom with verification')
    print('R  - Send raw uplink')
    print('IT - Perform image TX testing')
    print('IP - Setup data for image test')
    print('D  - Perform delay testing')
    print('H  - History readout')

    x = input().upper()

    if x == "C":
        UserEECPY()

def customUplink():
    global planedUplinks

    os.system('cls' if os.name == 'nt' else 'clear')

    print('Are you sure. Do you realy want create uplink (yes/NO):')
    x = input()

    if x != "yes":
        return

    print('Do you want read or write?:')
    x = input()

    if x != "read" and x != "write":
        return

    print('Address in EEPROM:')
    addr = int(input())

    data = ""
    size = ""
    raw  = "no"
    dr   = 0
    fr   = 0
    if x == "write":
        print('HEX Data to write (one byte):')
        data = int(input(), 16)

        planedUplinks.append({
            "data":     EEWRITE(addr, data),
            "expected": None, # readout check
            "status":   "Custom write " + str(addr) + " = " + str(data),
            "send":     True
        })

    else:
        print('Readout size:')
        size = int(input())
        print("Raw readout? (yes/NO):")
        raw = input()
        if raw == "yes":
            print("Datarate index:")
            dr = int(input()) * 3
            print("Freq index:")
            fr = int(input()) * 3

        planedUplinks.append({
            "data":     EEREAD(addr, size, raw == "yes", rawFRP = fr, rawDRP = dr),
            "expected": None, # readout check
            "status":   "Custom read from " + str(addr),
            "send":     True
        })
    
def updateFiles():
    pass

def loraWanMac(macCommands, devAddr):
    comLen = int(len(macCommands) / 2)

    if (comLen > 15):
        print("ERROR too long uplink message " + macCommands)
        exit()

    return f"60{devAddr}{toHex(comLen, 2)}{toHex(upCounter, 4)}{macCommands}00"

def addToCsv(filename, data):
    if not(os.path.exists(filename)):
        with open(filename, "w") as f:  
            for k, v in data.items():
                f.write(f"{k};")

            f.write("\n")

    with open(filename, "a") as f:  
        for k, v in data.items():
            f.write(f"{v};")

        f.write("\n")

def updateLastInCsv(filename, data):
    rawText = ""
    for k, v in data.items():
        rawText += f"{v};"

    rawText += "\n"

    lines = open(filename, 'r').readlines()
    lines[-1] = rawText
    out = open(filename, 'w')
    out.writelines(lines)
    out.close()


def parseTelemetry(data, time, pkt):
    global lastTelemetry

    lastTelemetry.append({
        # RST SOLAR TEMP PRESS  HPTR
        # 01  03    35   F5     01
        "rst":      int(data[0] + data[1], 16),
        "solar":    (int(data[2] + data[3], 16) / 255) * 2.56,
        "temp":     twos_complement(data[4] + data[5], 8) / 2,
        "press":    int(data[6] + data[7], 16) * 4,
        "hptr":     int(data[8] + data[9], 16),
        "alt":      44330 * (1.0 - math.pow((int(data[6] + data[7], 16) * 4) / BASELINE, 0.1903)),
        "lat":      pkt["lat"],
        "lon":      pkt["lon"],
        "gs":       pkt["gs"],
        "stations": pkt["stations"],
        "time":  time
    })

    # save telemetry to file
    addToCsv("telemetry.csv", lastTelemetry[-1])

def confirmUplink(data): # uplink is confirmed using expexted downlink
    global planedUplinks
    global status
    global lastUplinkTime

    dt = datetime.datetime.now(datetime.timezone.utc)
    utc_time = dt.replace(tzinfo=datetime.timezone.utc)
    utc_timestamp = int(utc_time.timestamp())

    if len(planedUplinks) > 0 and not planedUplinks[0]["send"] and (planedUplinks[0]["uplink"] is None or planedUplinks[0]["uplink"].isDone()):
        if data.upper() == planedUplinks[0]["expected"].upper(): # plan next
            if lastUplinkTime == 0:
                lastUplinkTime = utc_timestamp - planedUplinks[0]["start"]

            lastUplinkTime = (lastUplinkTime + (utc_timestamp - planedUplinks[0]["start"])) / 2

            planedUplinks.pop(0)
            status = "Uplink confirmed"

        else: # plan same
            planedUplinks[0]["send"] = True
            status = "Uplink not confirmed"

def parseLoraWan(pkt):
    decoded  = subprocess.run(['./lorawan-parser/lorawan-parser.py', '--appskey', args.lw_appkey, pkt["data"]], stdout=subprocess.PIPE).stdout.decode('utf-8').split("\n")
    devAddr  = ""
    fCnt     = ""
    fPort    = ""
    fAppData = ""

    for line in decoded:
        if line.startswith("    DevAddr : x "):
            devAddr  = line.replace("    DevAddr : x ", "").upper()

        if line.startswith("    FCnt : "):
            fCnt     = int(line.replace("    FCnt : ", ""))

        if line.startswith("  AppData : x "):
            fAppData = line.replace("  AppData : x ", "").upper()

        if line.startswith("    FPort : "):
            fPort    = int(line.replace("    FPort : ", ""))

    reversalDevAddr = "".join([DEV_ADDR[idx:idx+2] for idx in range(len(DEV_ADDR)) if idx % 2 == 0][::-1])

    if devAddr != reversalDevAddr or fCnt == "" or fPort == "" or fAppData == "":
        return None

    LWPkt = pkt.copy()

    LWPkt["data"]       = fAppData
    LWPkt["port"]       = fPort
    LWPkt["counter"]    = fCnt
    LWPkt["lat"]        = 0
    LWPkt["lon"]        = 0
    LWPkt["gs"]         = 0
    LWPkt["stations"]   = 1
    LWPkt["rawMessage"] = ""

    return LWPkt

def TTNOnMessage(mosq, obj, msg):
    data = json.loads(msg.payload.decode("ascii"))

    if "uplink_message" not in data:
        return # not a downlink message

    if data["end_device_ids"]["device_id"] != args.ttn_dev:
        return # not taget device


    LWPkt = {}

    #time;data;snr;ferror;rssi;port;counter;direction;

    LWPkt["data"]    = base64.b64decode(data["uplink_message"]["frm_payload"]).hex().upper()

    # 2024-09-10T19:01:05.165153318Z
    LWPkt["time"]    = int(datetime.datetime.strptime(data["uplink_message"]["received_at"].split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp())
    LWPkt["snr"]     = data["uplink_message"]["rx_metadata"][0]["snr"]
    LWPkt["rssi"]    = data["uplink_message"]["rx_metadata"][0]["rssi"]

    LWPkt["ferror"]  = 0 # no data

    for metadata in data["uplink_message"]["rx_metadata"]: # find best
        LWPkt["snr"]  = min(metadata["snr"],  LWPkt["snr"])
        LWPkt["rssi"] = min(metadata["rssi"], LWPkt["rssi"])

    LWPkt["port"]    = data["uplink_message"]["f_port"]
    LWPkt["counter"] = data["uplink_message"]["f_cnt"]
    LWPkt["lat"], LWPkt["lon"], LWPkt["gs"] = 0, 0, 1

    LWPkt["stations"] = len(data["uplink_message"]["rx_metadata"])
    LWPkt["rawMessage"] = base64.b64encode(json.dumps(data).encode('utf-8')).decode('utf-8')

    # use GPS to get best pressure readings
    #updateBaseline(LWPkt["lat"], LWPkt["lon"])
    
    processNewPacket(LWPkt)

def processNewPacket(pkt):
    global packetsCount
    global lastPackets
    global lastTelemetry
    global lastPacketTime
    global lastPacketSnr
    global lastFError
    global lastPacketRSSI

    pkt["direction"] = "down"

    if pkt["time"] > lastPacketTime:
        lastPacketTime = pkt["time"]
        lastPacketSnr  = pkt["snr"]
        lastFError     = pkt["ferror"]
        lastPacketRSSI = pkt["rssi"]

    if len(lastLWPackets) > 0 and lastLWPackets[-1]["counter"] == pkt["counter"]: # duplicity
        
        # update count of received
        lastLWPackets[-1]["stations"] += pkt["stations"]
        
        # update LAT and LON
        if lastLWPackets[-1]["lat"] == 0 and lastLWPackets[-1]["lon"] == 0:
            lastLWPackets[-1]["lat"] = pkt["lat"]
            lastLWPackets[-1]["lon"] = pkt["lon"]
            lastLWPackets[-1]["gs"]  = pkt["gs"]

        # add raw data
        if lastLWPackets[-1]["rawMessage"] == "":
            lastLWPackets[-1]["rawMessage"] = pkt["rawMessage"]

        # freq shift if yags
        if lastLWPackets[-1]["ferror"] == 0:
            lastLWPackets[-1]["ferror"] = pkt["ferror"]
        
        # update telemetry
        lastTelemetry[-1]["lat"]      = lastLWPackets[-1]["lat"]
        lastTelemetry[-1]["lon"]      = lastLWPackets[-1]["lon"]
        lastTelemetry[-1]["gs"]       = lastLWPackets[-1]["gs"]
        lastTelemetry[-1]["stations"] = lastLWPackets[-1]["stations"]

        # update last lines in files
        updateLastInCsv("telemetry.csv", lastTelemetry[-1])
        updateLastInCsv("packet.csv"   , lastLWPackets[-1])

        return

    lastLWPackets.append(pkt)
    
    # save packet to file
    addToCsv("packet.csv", pkt)

    packetsCount += 1

    # on port 2 is read
    if pkt["port"] == 2:
        confirmUplink(pkt["data"]) # try confirm uplink
    else:
        confirmUplink("") # dummy data fail to confirm

    # on port 1 is telemetry
    if pkt["port"] == 1:
        parseTelemetry(pkt["data"], pkt["time"], pkt)

def processNewRawPacket(pkt):
    global lastRawPacketTime

    lastPackets.append(pkt)

    lastRawPacketTime = pkt["time"]

    lwpkt = parseLoraWan(pkt)

    if lwpkt is not None:
        processNewPacket(lwpkt)

def updateTUI():
    os.system('cls' if os.name == 'nt' else 'clear')

    dt = datetime.datetime.now(datetime.timezone.utc)
    utc_time = dt.replace(tzinfo=datetime.timezone.utc)
    utc_timestamp = int(utc_time.timestamp())

    print(f"LAST SNR: {lastPacketSnr}; LAST SEEN: before {utc_timestamp - lastPacketTime}s")
    print("Saving telemetry to telemetry.csv and all LW packets to packets.csv")
    print(f"Uplinks: {upCounter}, Downlinks: {packetsCount}, Planed Uplinks: {len(planedUplinks)}, Time per uplink: {lastUplinkTime / 60}m, Need time for uplinks: {(lastUplinkTime * len(planedUplinks)) / 60}m")
    print("")
    print("LAST TELEMETRY:")
    print("Temperature (C)       Pressure (hPA)        Solar (V)        Resets        History PTR        ALT (m)       LAT           LON           GS PRESSION           BEFORE")

    for i in range(min(len(lastTelemetry), 5)):
        actPacket = lastTelemetry[len(lastTelemetry) - 1 - i]
        print(f"{actPacket["temp"]: <22}{actPacket["press"]: <22}{round(actPacket["solar"], 2): <17}{actPacket["rst"]: <14}{actPacket["hptr"]: <19}{round(actPacket["alt"], 1): <14}{round(actPacket["lat"], 5): <14}{round(actPacket["lon"], 5): <14}{actPacket["gs"]: <22}{utc_timestamp - actPacket["time"]:<3}s")

    print("")
    print("PLANED UPLINKS (FISRT 5):")

    for i in range(min(len(planedUplinks), 5)):
        uplinkStatus = ""
        if planedUplinks[i]["send"] == False:
            uplinkStatus = "Waiting from confirmation"

        print(f"{planedUplinks[i]["data"]: <50}        {planedUplinks[i]["status"]}        {uplinkStatus}")

    print("")
    print("RECEIVED RAW DOWNLINKS (LAST 5):")

    for i in range(min(5, len(lastPackets))):
        actPacket = lastPackets[len(lastPackets) - 1 - i]
        print(f"{actPacket["data"]: <50}        BEFORE: {utc_timestamp - actPacket["time"]:<4}s SNR: {actPacket["snr"]:<6}  FERROR: {actPacket["ferror"]:<10}  RSSI:    {actPacket["rssi"]:<7}")

    print("")
    print("RECEIVED LORAWAN DOWNLINKS (LAST 5):")
    for i in range(min(5, len(lastLWPackets))):
        actPacket = lastLWPackets[len(lastLWPackets) - 1 - i]

        if actPacket["rawMessage"] != "" and actPacket["ferror"] != 0:
            rcvFlags = "[YAGS TTN]"
        elif actPacket["rawMessage"] == "":
            rcvFlags = "[YAGS]"
        else:
            rcvFlags = "[TTN]"

        print(f"{actPacket["data"]: <50}        BEFORE: {utc_timestamp - actPacket["time"]:<4}s SNR: {actPacket["snr"]:<6}  PORT:   {actPacket["port"]:<10}    COUNTER: {actPacket["counter"]:<7}    STATIONS: {actPacket["stations"]:<3}    {rcvFlags}")

    print("")
    print(f"Status: {status}        E start EEPROM setup        U custom uplink      T on/off TNN for uplinks     A advanced")
    
    if TTNForDownLink:
        print("Y Using TTN for uplinks")
    else:
        print("N NOT using TTN for uplinks")

    print("Write char and hit enter!")

mqttc = None

if args.ttn_host is not None:
    mqttc = mqtt.Client()

    # Add message callbacks that will only trigger on a specific subscription match.
    mqttc.on_message = TTNOnMessage
    mqttc.username_pw_set(
        args.ttn_app,
        args.ttn_api
    )
    mqttc.connect(
        args.ttn_host,
        1883,
        60
    )
    mqttc.subscribe("#", 0)

    mqttc.loop_start()

while True:
    dt = datetime.datetime.now(datetime.timezone.utc)
    utc_time = dt.replace(tzinfo=datetime.timezone.utc)
    utc_timestamp = int(utc_time.timestamp())

    # proccess uplinks
    if len(planedUplinks) > 0 and planedUplinks[0]["send"]:
        uplinkJob   = None
        validUplink = True

        if not TTNForDownLink and sat is not None:
            uplinkJob = sat.planUplink(station, loraWanMac(planedUplinks[0]["data"], DEV_ADDR), delay=RX_WINDOW_DELAY)
        elif TTNForDownLink and mqttc is not None:
            mqttc.publish(f"v3/{args.ttn_app}/devices/{args.ttn_dev}/down/replace", '{"downlinks":[{"f_port": 1,"frm_payload":"' + base64.b64encode(bytes.fromhex(planedUplinks[0]["data"])).decode("ascii") + '","priority": "NORMAL"}]}')
        else:
            status = "unable to plan Uplink"
            validUplink = False

        if validUplink:
            upCounter += 1

            if planedUplinks[0]["status"] is not None:
                status = planedUplinks[0]["status"]

            if planedUplinks[0]["expected"] is not None:
                planedUplinks[0]["send"]   = False
                planedUplinks[0]["uplink"] = uplinkJob
                planedUplinks[0]["start"]  = utc_timestamp
            else:
                planedUplinks.pop(0)

    packets = []
    
    if ob is not None:
        packets = ob.getPackets()

    for packet in packets:
        if packet["time"] > lastRawPacketTime:
            processNewRawPacket(packet)

    updateTUI()

    # remeve old packets
    if len(lastPackets) > 5:
        lastPackets.pop(0)

    if len(lastLWPackets) > 5:
        lastLWPackets.pop(0)

    if len(lastTelemetry) > 5:
        lastTelemetry.pop(0)

    i, o, e = select.select([sys.stdin], [], [], 3)

    if (i): # process command
        command = sys.stdin.readline().strip().upper()

        if command == "E":
            setupEEPROM()

        if command == "U":
            customUplink()

        if command == "T":
            TTNForDownLink = not(TTNForDownLink)

        if command == "A":
            advanced()
