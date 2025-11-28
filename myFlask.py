from flask import Flask, render_template, redirect, session, url_for, request
from authlib.integrations.flask_client import OAuth
import markdown
from flask_session import Session
import google.generativeai as genai
import requests
import pymongo
from dotenv import load_dotenv
import os

'''
            Loading variables from .env file
'''
load_dotenv()   #loading the .env file from the same directory || load_dotenv(env_path) for .env in parent dir
atlas_string = os.getenv('atlas_string')
Gemini_key = os.getenv('gemini_api_key')
client_ID = os.getenv('client_id')
client_secret = os.getenv('client_secret')


client = pymongo.MongoClient(atlas_string)
# client = pymongo.MongoClient("mongodb://localhost:27017")
db = client["Google_OAuth"]
collection = db["Health_Guide_Chatbot_CHATS"]
collection2 = db["Health_Guide_Chatbot_USERINFO"]

def Gemini(prompt):
    genai.configure(api_key=Gemini_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(prompt)
    return response.text

app = Flask(__name__)
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = True
Session(app)

oauth = OAuth(app)

oauth.register(
    "myApp",
    client_id = client_ID,
    client_secret = client_secret,
    server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs ={
        "scope": "profile email openid https://www.googleapis.com/auth/user.birthday.read https://www.googleapis.com/auth/user.gender.read"
    }
)


@app.route('/')
def Index():
    return render_template("index3.html")

@app.route('/app', methods=['POST', 'GET'])
def chat():
    if "user" not in session:
        return redirect('/login')
    
    return render_template("index.html", messages=session.get("chats"), username=session["user"]["userinfo"]["name"], email=session["user"]["userinfo"]["email"])

@app.route('/fetch_msg_from_mongoDB/<email>/<user_input>')
def fetch_msg(email, user_input):
    Msg_List = collection.find_one({"email":session["user"]["userinfo"]["email"]})
    if Msg_List:
        myList = Msg_List["Msg_list"] #If the document is there take the chat-list from there || If not then create it from start
    else:
        system_instruction = (
                "You are a professional and helpful health assistant. "
                "Your purpose is to provide general, medicinal information and lifestyle tips, and diagnose the user for some issue. "
                "You **must** ask for extra details wherever required from the user before diagnosing them. "
                "and that they should consult a professional for medical advice, and should not solely rely on your advice "
                "Do not engage in conversations outside of health, wellness, or general information."
            )

        myList = [
                {"role": "user", "parts": [{"text": system_instruction}]},

                # The model's first visible message
                {"role": "model", "parts": [{"text": "Hi there! I'm ready to help you with general health and wellness information. What's on your mind today?"}]}
            ]
    myList.append({"role":"user", "parts":[{"text":user_input}]})
    Bot_response = Gemini(myList)
    Bot_response_html = markdown.markdown(Bot_response)  # 🟢 CONVERT MARKDOWN TO HTML HERE 🟢
    myList.append({"role":"model", "parts":[{"text":Bot_response_html}]})
    collection.update_one(
        {"email": session["user"]["userinfo"]["email"]},
        {"$set": {"Msg_list": myList}},
        upsert=True #If no matching document mongoDB creates one, if it exists mongoDB creates it.
    )
    session["chats"] = myList
    return render_template("chat_section.html", messages=myList)
    #create a partials/html file. Return it as render_template() to fetch. JS will insert adjacentHTML. And I am giving the Dict. with the render_template('partials', session=myList) so that JS just gives a fetch command and take the HTML and inserts it. Just like the portFolio site. 


#This is from where the oauth will send the user to google-OAUTH2.0 and will send the params
@app.route('/login')
def Login():
    if "user" in session:
        return redirect('/app')
    return oauth.myApp.authorize_redirect(redirect_uri=url_for("callback", _external=True))

#This is where we'll receive the authorization-code from google and will be able to call the token using that
#We do not have to do request.args() separately the oauth handels it automatically with flask😋 Hurray
#Remember a GET request is automatically acepted by a flask route but not a POST request
@app.route('/callback')
def callback():
    error = request.args.get("error")
    if error:
        # They denied one or more consents
        return redirect('/no_consent')
    
    # THIS IS TO BLOCK THE USER IF THE USER DO NOT GIVE THE CONSENT
    
    # scopes = request.args.get("scope")
    # if scopes:
    #     scope_list = scopes.split()
    #     if "https://www.googleapis.com/auth/user.gender.read" in scope_list and "https://www.googleapis.com/auth/user.gender.read":
    #         pass
    #     else:
    #         return redirect('/no_consent')
    # else:
    #     return redirect('/no_consent')

    token = oauth.myApp.authorize_access_token()
    
    #Now we've got the access_token of the user we can do something with it
    access_token = token["access_token"]
    response = requests.get(
        "https://people.googleapis.com/v1/people/me?personFields=genders,birthdays",
        headers={
            "Authorization": f"Bearer {access_token}"
        }
    ).json()
    token["personData"] = response
    session["user"] = token
    email = session["user"]["userinfo"]["email"]
    doc = collection2.find_one({"email":email}) # Checking if this user loged In before!! If yes then delete the older document and then add a new one
    if not doc:
        collection2.insert_one({"email":email , f"{email}":token})
    else:
        collection2.delete_one({"email":email})
        collection2.insert_one({"email":email , f"{email}":token})
    Msg_List = collection.find_one({"email":email}) #deleting session 'chats' as we logout and adding as we log in
    if Msg_List:
        myList = Msg_List["Msg_list"]
        session["chats"] = myList
    return redirect('/app')

@app.route('/logout')
def logout():
    if "user" in session:
        session.clear()  # This will clear the session 'user' and 'chats' else space in my filesystem will be filled
        return redirect('/')
    else:
        return redirect('/')

@app.route('/clear_chat')
def clear():
    if "user" not in session:
        return redirect('/login')

    collection.delete_one({"email":session["user"]["userinfo"]["email"]})
    if "chats" in session:
        session.pop("chats")
    return redirect('/app')

@app.route('/no_consent')
def no_consent():
    return render_template("index2.html")

