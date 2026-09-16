from utils import db
db.init_db()
db.update_lead_status(68, 'researched')
print('Lead 68 ready for design')