from aiohttp import web

import os
import pint
import ssl
import yaml
import paho.mqtt.client as mqtt
import json
import argparse
import time
import logging

ureg = pint.UnitRegistry()

imperalUnits = {"km": "mi", "°C": "°F", "km/h": "mph", "m": "ft"}

prettyPint = {
    "degC": "°C",
    "degF": "°F",
    "mile / hour": "mph",
    "kilometer / hour": "km/h",
    "mile": "mi",
    "kilometer": "km",
    "meter": "m",
    "foot": "ft",
}

assumedUnits = {
    "04": "%",
    "05": "°C",
    "0c": "rpm",
    "0d": "km/h",
    "0f": "°C",
    "11": "%",
    "1f": "km",
    "21": "km",
    "2f": "%",
    "31": "km",
}

assumedShortName = {
    "04": "engine_load",
    "05": "coolant_temp",
    "0c": "engine_rpm",
    "0d": "speed",
    "0f": "intake_temp",
    "11": "throttle_pos",
    "1f": "run_since_start",
    "21": "dis_mil_on",
    "2f": "fuel",
    "31": "dis_mil_off",
}

assumedFullName = {
    "04": "Engine Load",
    "05": "Coolant Temperature",
    "0c": "Engine RPM",
    "0d": "Vehicle Speed",
    "0f": "Intake Air Temperature",
    "11": "Throttle Position",
    "1f": "Distance Since Engine Start",
    "21": "Distance with MIL on",
    "2f": "Fuel Level",
    "31": "Distance with MIL off",
}

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))# get an instance of the logger object this module will use

data = {}
publish_counter = 0
published_counter = 0
mqttformat = "json"


def prettyUnits(unit):
    if unit in prettyPint:
        return prettyPint[unit]

    return unit


def unprettyUnits(unit):
    for pint_unit, pretty_unit in prettyPint.items():
        if pretty_unit == unit:
            return pint_unit

    return unit


def convertUnits(value, u_in, u_out):
    q_in = ureg.Quantity(value, u_in)
    q_out = q_in.to(u_out)
    return {"value": round(q_out.magnitude, 2), "unit": str(q_out.units)}


def prettyConvertUnits(value, u_in, u_out):
    p_in = unprettyUnits(u_in)
    p_out = unprettyUnits(u_out)
    res = convertUnits(value, p_in, p_out)
    return {"value": res["value"], "unit": prettyUnits(res["unit"])}


async def process_torque(request):
    session = parse_fields(request.query)
    publish_data(session)
    return web.Response(text="OK!")


def parse_fields(qdata):  # noqa
    session = qdata.get("session")
    if session is None:
        raise Exception("No Session")

    if session not in data:
        data[session] = {
            "profile": {},
            "unit": {},
            "defaultUnit": {},
            "fullName": {},
            "shortName": {},
            "value": {},
            "unknown": [],
            "time": 0,
        }

    for key, value in qdata.items():
        if key.startswith("userUnit"):
            continue
        if key.startswith("userShortName"):
            item = key[13:]
            data[session]["shortName"][item] = value
            continue
        if key.startswith("userFullName"):
            item = key[12:]
            data[session]["fullName"][item] = value
            continue
        if key.startswith("defaultUnit"):
            item = key[11:]
            data[session]["defaultUnit"][item] = value
            continue
        if key.startswith("k"):
            item = key[1:]
            if len(item) == 1:
                item = "0" + item
            data[session]["value"][item] = value
            continue
        if key.startswith("profile"):
            item = key[7:]
            data[session]["profile"][item] = value
            continue
        if key == "eml":
            data[session]["profile"]["email"] = value
            continue
        if key == "time":
            data[session]["time"] = value
            continue
        if key == "v":
            data[session]["profile"]["version"] = value
            continue
        if key == "session":
            continue
        if key == "id":
            data[session]["profile"]["id"] = value
            continue

        data[session]["unknown"].append({"key": key, "value": value})

    return session


def slugify(name):
    return (
        name.lower()
        .replace("(", " ")
        .replace(")", " ")
        .strip()
        .replace(" ", "_")
    )


def get_field(session, key):
    name = data[session]["fullName"].get(key, assumedFullName.get(key, key))
    short_name = data[session]["shortName"].get(
        key, assumedShortName.get(key, key)
    )
    unit = data[session]["defaultUnit"].get(key, assumedUnits.get(key, ""))
    value = data[session]["value"].get(key)
    short_name = slugify(short_name)

    if config.get("imperial") is True:
        if unit in imperalUnits:
            conv = prettyConvertUnits(float(value), unit, imperalUnits[unit])
            value = conv["value"]
            unit = conv["unit"]

    return {
        "name": name,
        "short_name": short_name,
        "unit": unit,
        "value": value,
    }


def get_profile(session):
    return data[session]["profile"]


def get_topic_prefix(session):
    topic = data[session]["profile"].get("Name")
    if topic is None:
        topic = data[session]["profile"].get("email")
    if topic is None:
        topic = session

    topic = slugify(topic)

    return config["mqtt"]["prefix"] + "/" + topic


def get_data(session):
    global mqttformat
    retdata = {}
    retdata["time"] = data[session]["time"]
    meta = {}

    for key, value in data[session]["value"].items():
        row_data = get_field(session, key)
        retdata[row_data["short_name"]] = row_data["value"]
        
        if mqttformat == "json": 
            meta[row_data["short_name"]] = {
                "name": row_data["name"],
                "unit": row_data["unit"],
            }

    if mqttformat == "json": 
        retdata["profile"] = get_profile(session)
        retdata["meta"] = meta

    return retdata


def publish_data(session):
    global mqttformat
    session_data = get_data(session)
    if mqttformat == "raw":
        logging.info("Publish mqtt values")
        for key, value in session_data.items():
            logging.info("Publish mqtt "+str(key)+" : "+str(value))
    else:
        mqttc.publish(get_topic_prefix(session), json.dumps(session_data),2,True)
    
    global publish_counter
    global published_counter
    publish_counter += 1
    # check if publish counter grows with no feedback
    # if yes create a new MQTT client
    if publish_counter - published_counter > 10:
        publish_counter = 0
        published_counter = 0
        logging.info("No publish results received - reconnect")
        mqttc_create()

mqttc = None
mqttc_time = time.time()


def mqtt_on_connect(client, userdata, flags, rc):
    if rc != 0:
        logging.info("MQTT Connection Issue")
        exit()


def mqtt_on_disconnect(client, userdata, rc):
    logging.info("MQTT Disconnected")
    if time.time() > mqttc_time + 10:
        mqttc_create()
    else:
        exit()

def mqtt_on_publish(client, userdata, id):
    global published_counter
    published_counter += 1

def mqttc_create():
    global mqttc
    global mqttc_time
    mqttc = mqtt.Client(client_id="torque", clean_session=True)
    mqttc.username_pw_set(
        username=config["mqtt"].get("username"),
        password=config["mqtt"].get("password"),
    )
    if config["mqtt"].get("cert"):
        logging.info("CERT: "+config["mqtt"].get("cert"))
        mqttc.tls_set(config["mqtt"].get("cert"), tls_version=ssl.PROTOCOL_TLS)

    logging.info("CALLING MQTT CONNECT")
    mqttc.connect(
        config["mqtt"]["host"], config["mqtt"].get("port", 1883), keepalive=60
    )
    mqttc.on_connect = mqtt_on_connect
    mqttc.on_disconnect = mqtt_on_disconnect
    mqttc.on_publish = mqtt_on_publish
    mqttc.loop_start()
    mqttc_time = time.time()


argparser = argparse.ArgumentParser()
argparser.add_argument(
    "-c",
    "--config",
    required=True,
    help="Directory holding config.yaml and application storage",
)
args = argparser.parse_args()

configdir = args.config
if not configdir.endswith("/"):
    configdir = configdir + "/"

with open(configdir + "config.yaml") as file:
    config = yaml.load(file, Loader=yaml.FullLoader)

mqttc_create()


if __name__ == "__main__":
    host = config.get("server", {}).get("ip", "0.0.0.0")
    port = config.get("server", {}).get("port", 5000)

    mqttformat = config.get("mqtt", {}).get("port", "json")
    
    app = web.Application()
    app.router.add_get("/", process_torque)
    web.run_app(app, host=host, port=port)
