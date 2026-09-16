import config
from agents import design_agent, sales_agent
from utils import db

def main():
    config.validate()
    db.init_db()
    
    # Grab the lead you manually set to 'researched'
    leads = db.list_leads_by_status("researched")
    if not leads:
        print("No 'researched' leads found. Run your skip_research script again first.")
        return
        
    lead = leads[0]
    print(f"Processing lead {lead['id']}: {lead['business_name']}")

    # Skip LeadAgent and go straight to DesignAgent
    print("--- Step 1: DesignAgent ---")
    website = design_agent.process_lead(lead)
    
    # Go straight to SalesAgent
    print("--- Step 2: SalesAgent ---")
    sales_agent.send_cold_email(lead)
    print("Done. Check your inbox.")

if __name__ == "__main__":
    main()