import json

def handler(request):
    if request.method == "POST":
        data = request.get_json()
        print(data)
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "ok"})
        }

    return {
        "statusCode": 200,
        "body": "Bot is running"
    }

