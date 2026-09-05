// Netlify Serverless Function: cameras.js
exports.handler = async function(event, context) {
  return {
    statusCode: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*"
    },
    body: JSON.stringify({
      cameras: [
        { id: 1, name: "CAM-01 Concourse & Hub", status: "LIVE" },
        { id: 2, name: "CAM-02 Platform 1 Track (YOLO)", status: "LIVE" },
        { id: 3, name: "CAM-03 North Foot Over Bridge", status: "LIVE" },
        { id: 4, name: "CAM-04 Turnstiles Gate 1", status: "LIVE" }
      ],
      max_checked: 6
    })
  };
};
