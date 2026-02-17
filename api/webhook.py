import json

def handler(request):
    if request.method == "POST":
        try:
            data = request.json()
            print(data)
        except:
            pass

        return {
            "statusCode": 200,
            "body": json.dumps({"status": "ok"})
        }

    return {
        "statusCode": 200,
        "body": "Bot is running"
    }
