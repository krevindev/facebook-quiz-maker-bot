# Simple in-memory user session store (stateless, resets on restart)
user_sessions = {}

def get_session(user_id):
    return user_sessions.get(user_id)

def set_session(user_id, data):
    user_sessions[user_id] = data

def clear_session(user_id):
    if user_id in user_sessions:
        del user_sessions[user_id]
