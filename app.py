from flask import Flask, request, jsonify
from script_france_travail import get_france_travail_jobs

app = Flask(__name__)

@app.route("/jobs", methods=["GET"])
def jobs():
    keyword = request.args.get("keyword")
    region_code = request.args.get("region_code")
    max_results = int(request.args.get("max_results", 50))

    jobs = get_france_travail_jobs(
        region_code=region_code,
        keyword=keyword,
        max_results=max_results
    )
    return jsonify(jobs)

if __name__ == "__main__":
    app.run(debug=True)
