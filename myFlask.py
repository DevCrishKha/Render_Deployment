"""
myFlask.py

WHAT'S NEW IN THIS VERSION:
Previously we stored the raw .docx file itself in MongoDB (via
GridFS). Now, instead, we CONVERT the Word file to HTML the moment
it's uploaded, and only the resulting HTML text gets saved into
MongoDB. The original .docx bytes are never kept anywhere.

WHY THIS MATTERS FOR RENDER HOSTING:
Render's filesystem is "ephemeral" - every time your app restarts or
redeploys, anything you wrote to local disk is gone (imagine a
microcontroller that wipes its flash on every reset - you wouldn't
store your calibration data there, you'd send it to an external
EEPROM instead). MongoDB is that external EEPROM here: it lives
outside your Render instance, so it survives restarts/redeploys.

HOW THE CONVERSION WORKS (the "mammoth" library):
  raw .docx bytes  --[mammoth.convert_to_html()]-->  HTML string
"mammoth" reads the Word file's internal structure (it's secretly a
zipped folder of XML files) and maps Word's built-in styles to plain
HTML tags:
    Word "Heading 1"   -> <h1>...</h1>
    Word "Heading 2"   -> <h2>...</h2>
    a normal paragraph -> <p>...</p>
    bold / italic text -> <strong>/<em>
We do NOT use "python-docx" for this part - python-docx is great for
reading/editing individual pieces of a Word file (paragraphs, runs,
tables) as Python objects, but it has no built-in "give me HTML"
function. mammoth is built specifically for the docx-to-HTML job.

WHERE THE FILES "COME FROM" (answering your question directly):
There's no folder to browse anymore - that's the point. Each
converted note becomes one MongoDB document that looks like:

    {
      "_id": ObjectId("..."),
      "filename": "physics_notes.docx",
      "html": "<h1>Physics Notes</h1><p>...</p>",
      "uploaded_at": <timestamp>,
      "size_bytes": 20480
    }

To "read" a file now, you don't open a path on disk - you query
MongoDB for that document and use its "html" field. That's exactly
what the /notes/<note_id> route below does.
------------------------------------------------------------------
"""

import os
from datetime import datetime, timezone

from flask import Flask, render_template, request
from pymongo import MongoClient
from bson.objectid import ObjectId
import mammoth

app = Flask(__name__)
app.secret_key = "change-this-to-something-random"  # needed for the dialog-box error message

# ------------------------------------------------------------------
# 1) CONNECT TO MONGODB
# ------------------------------------------------------------------
# On Render, set MONGO_URI as an "Environment Variable" in your
# service's dashboard (Settings -> Environment). Never hard-code your
# real password into this file.
MONGO_URI = os.getenv("Atlas_string1")
DB_NAME = "Word-To-HTML-Notes"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# One normal MongoDB collection to hold the converted notes.
# (We dropped GridFS entirely - HTML text is small, so it fits
# comfortably inside a single ordinary document, no chunking needed.)
notes_collection = db["notes"]

# ------------------------------------------------------------------
# 2) THE SIZE LIMIT (unchanged - still checked on the ORIGINAL
#    .docx bytes, before conversion)
# ------------------------------------------------------------------
MAX_TOTAL_BYTES = 30 * 1024 * 1024  # 30MB


@app.route("/", methods=["GET", "POST"])
def index():
    return "Hello World !!"


# ------------------------------------------------------------------
# 3) LIST + VIEW NOTES
# ------------------------------------------------------------------
@app.route('/notes')
def see_notes():
    # Pull just the id/filename/date for every note - not the full
    # html - so this list page loads fast.
    docs = notes_collection.find({}, {"filename": 1, "uploaded_at": 1}).sort("uploaded_at", -1)
    notes = [{"id": str(d["_id"]), "filename": d["filename"], "uploaded_at": d["uploaded_at"]} for d in docs]
    return render_template("notes_output.html", notes=notes)


@app.route('/notes/all')
def view_all_notes():
    # Unlike see_notes() above, here we DO need the full "html" field
    # for every note, because we're putting all of them on one page.
    # Still sorted so the newest upload shows first.
    docs = notes_collection.find({}).sort("uploaded_at", -1)
    notes = list(docs)  # pull everything into a normal Python list
    return render_template("all_notes.html", notes=notes)


@app.route('/notes/<note_id>')
def view_note(note_id):
    # This is the Jinja-templating step you asked about: we fetch the
    # stored HTML string from MongoDB, then hand it to the template
    # as a normal variable. Inside the template it's inserted with
    # {{ note.html | safe }} - the "| safe" tells Jinja "this text IS
    # HTML on purpose, don't escape the < and > characters."
    doc = notes_collection.find_one({"_id": ObjectId(note_id)})
    if not doc:
        return "Note not found", 404
    return render_template("notes_output.html", note=doc, notes=None)


@app.route('/notes_plain/<note_id>')
def notes_plain(note_id):
    # The "plain" version: no page chrome, just the converted HTML
    # exactly as mammoth produced it, served directly as a webpage.
    doc = notes_collection.find_one({"_id": ObjectId(note_id)})
    if not doc:
        return "Note not found", 404
    return doc["html"]


# ------------------------------------------------------------------
# 4) THE UPLOAD PAGE (now converts to HTML before storing)
# ------------------------------------------------------------------
@app.route('/upload', methods=['GET', 'POST'])
def upload_files():
    if request.method == 'GET':
        return render_template("upload_notes.html", error=None)

    uploaded_files = request.files.getlist("word_files")
    uploaded_files = [f for f in uploaded_files if f and f.filename]

    if not uploaded_files:
        return render_template(
            "upload_notes.html",
            error="No file was selected. Please choose at least one .docx file."
        )

    # --- Step A: read every file once, add up total size ---
    file_blobs = []
    total_size = 0
    for f in uploaded_files:
        raw = f.read()
        total_size += len(raw)
        file_blobs.append((f.filename, raw))

    # --- Step B: enforce the 30MB limit (checked BEFORE any
    #     conversion or database work happens) ---
    if total_size > MAX_TOTAL_BYTES:
        size_in_mb = round(total_size / (1024 * 1024), 2)
        return render_template(
            "upload_notes.html",
            error=f"Upload rejected: total size is {size_in_mb}MB, "
                  f"which is over the 30MB limit."
        )

    # --- Step C: convert each file to HTML, then store the HTML ---
    saved = []
    conversion_warnings = []
    for filename, raw in file_blobs:
        # mammoth wants a file-like object, so we wrap the raw bytes
        from io import BytesIO
        result = mammoth.convert_to_html(BytesIO(raw))
        html_output = result.value  # the actual <h1>...</h1><p>...</p> string

        # mammoth also reports anything it couldn't convert cleanly
        # (e.g. an unusual style) - worth surfacing, not fatal
        if result.messages:
            conversion_warnings.extend(str(m) for m in result.messages)

        inserted = notes_collection.insert_one({
            "filename": filename,
            "html": html_output,
            "uploaded_at": datetime.now(timezone.utc),
            "size_bytes": len(raw),
        })
        saved.append(str(inserted.inserted_id))

    success_msg = (
        f"Converted and stored {len(saved)} file(s) "
        f"({round(total_size / (1024*1024), 2)}MB total)."
    )
    if conversion_warnings:
        success_msg += f" Note: {len(conversion_warnings)} formatting warning(s) during conversion."

    return render_template("upload_notes.html", error=None, success=success_msg)


if __name__ == "__main__":
    app.run(debug=True)