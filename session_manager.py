user_sessions = {}

def get_session(user_id):
    return user_sessions.get(user_id)

def set_session(user_id, data):
    user_sessions[user_id] = data

def clear_session(user_id):
    user_sessions.pop(user_id, None)
