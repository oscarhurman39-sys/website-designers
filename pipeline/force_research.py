import config
from utils import db

def force_research():
    # Get all leads that haven't been 'designed' yet
    leads = db.list_all_leads()
    for lead in leads:
        if lead['status'] in ['new', 'lost']:
            print(f"Forcing lead {lead['id']} ({lead['business_name']}) to 'researched'...")
            
            # Inject default data so the DesignAgent has something to work with
            db.update_lead_fields(
                lead['id'],
                contact_email=config.ADMIN_EMAIL, # Fallback to your email if none found
                pain_point="Outdated website presence",
                testimonial="Great service, highly recommended!"
            )
            # Force the status to researched so the DesignAgent picks it up
            db.update_lead_status(lead['id'], 'researched', notes="Forced research by force_research.py")

if __name__ == "__main__":
    db.init_db()
    force_research()
    print("All 'new' or 'lost' leads are now ready for design.")