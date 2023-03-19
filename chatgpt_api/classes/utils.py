import json


def sse_pack(event, data):
    # Format data as an SSE message
    packet = "event: %s\n" % event
    packet += "data: %s\n" % json.dumps(data)
    packet += "\n"
    return packet
