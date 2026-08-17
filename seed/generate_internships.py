"""Internships — lightweight 6th dataset (clean, no deliberate fragmentation).

The internship portal is the one modern, well-maintained system in this mock
AICTE world: a single clean table with no duplicate rows and no noisy names.
It is linked to institutions via the CANONICAL registry name, and the
pipeline (stage 08) resolves that to the canonical institution_id after
entity resolution — no fragmentation needed, per the design decision.

Output: data/internships.csv (columns below). Deterministic per seed.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from faker import Faker

OUT_PATH = PROJECT_ROOT / "data" / "internships.csv"

TARGET_ROWS = 650

# domain pool — 200+ variety via base areas x focus roles
DOMAIN_AREAS = [
    "Data Science", "Machine Learning", "Artificial Intelligence", "Web Development",
    "Mobile App Development", "Cloud Engineering", "Cybersecurity", "DevOps",
    "UI/UX Design", "Product Management", "Business Analytics", "Financial Analysis",
    "Digital Marketing", "Content Writing", "Public Policy", "Civil Engineering",
    "Mechanical Design", "Electrical Engineering", "Embedded Systems", "Robotics",
    "Automobile Engineering", "Aerospace Engineering", "Biotechnology", "Chemical Engineering",
    "Structural Analysis", "Supply Chain Management", "Operations Management",
    "Human Resources", "Corporate Law", "Environmental Science", "Renewable Energy",
    "VLSI Design", "Networking", "Database Administration", "Blockchain Development",
    "Game Development", "Computer Vision", "Natural Language Processing",
    "Internet of Things", "Data Engineering", "Full-Stack Development", "Sales and Marketing",
    "Market Research", "Accounting", "Event Management", "Graphic Design", "Video Editing",
    "Journalism", "Social Work", "Healthcare Management", "Pharmaceutical Research",
    "Architecture Design", "Urban Planning", "Interior Design", "Fashion Design",
    "Agriculture Technology", "Food Technology", "Textile Engineering", "Mining Engineering",
    "Petroleum Engineering", "Nanotechnology", "Bioinformatics", "Clinical Research",
    "Telecommunications", "Satellite Communication", "Smart Grid", "Electric Vehicles",
    "Battery Technology", "Water Treatment", "GIS Mapping", "Construction Management",
    "Quality Assurance", "3D Printing", "Industrial Automation", "Instrumentation",
    "HVAC Design", "Marine Engineering", "Polymer Science", "Dairy Technology",
    "Fisheries Science", "Public Health", "Health Informatics", "Biomedical Engineering",
    "Sports Analytics", "Ayurveda Research", "Pharmacovigilance", "Drug Discovery",
    "Molecular Biology", "Microbiology", "Neuroscience", "Economics Research",
    "Policy Analysis", "Data Journalism", "Advertising", "Brand Management",
    "E-commerce", "Logistics", "Aviation Management", "Railway Operations",
    "Urban Mobility", "Highway Engineering", "Hydrology", "Geology", "Climate Modeling",
    "Sustainability Consulting", "Carbon Accounting", "Green Buildings", "Solar Energy",
    "Wind Energy", "Nuclear Engineering", "Optics", "Robotic Process Automation",
    "Chatbot Development", "Speech Recognition", "Image Processing", "Predictive Maintenance",
    "Digital Twin", "Augmented Reality", "Virtual Reality", "Cloud Security",
    "Penetration Testing", "Threat Intelligence", "Data Privacy", "Audit and Compliance",
    "Risk Management", "Actuarial Science", "Banking Operations", "Investment Banking",
    "Algorithmic Trading", "Credit Risk", "Fraud Detection", "Financial Modelling",
    "Corporate Governance", "Intellectual Property", "Patent Research", "Legal Research",
    "Tax Law", "Criminology", "Forensic Psychology", "User Research", "Service Design",
    "Motion Graphics", "3D Modelling", "Animation", "VFX", "Sound Design",
    "Technical Writing", "Scientific Writing", "Grant Writing", "Publishing",
    "Library Science", "Archaeology", "Sociology Research", "Political Science",
    "International Relations", "Defence Analysis", "Space Policy", "Drone Technology",
    "Autonomous Vehicles", "Photogrammetry", "Radar Systems", "Underwater Robotics",
    "Oceanography", "Marine Biology", "Wildlife Conservation", "Forestry",
    "Biodiversity Assessment", "Environmental Impact Assessment", "E-waste Recycling",
    "Biofuels", "Hydroponics", "Vertical Farming", "Smart Agriculture", "Agri-finance",
    "Rural Development", "Microfinance", "Digital Banking", "Fintech Compliance",
    "Smart Contracts", "Web3", "Crypto Research", "Payment Security", "AML Compliance",
    "KYC Operations", "Customer Support", "IT Service Management", "Change Management",
    "Site Reliability", "Data Visualization", "Business Intelligence", "Data Warehousing",
    "ETL Pipelines", "Data Governance", "Feature Engineering", "MLOps", "A/B Testing",
    "Recommendation Systems", "Search Relevance", "Ad Tech", "Customer Analytics",
    "Churn Prediction", "Market Segmentation", "Pricing Optimization", "Demand Forecasting",
    "Warehouse Automation", "Route Optimization", "Fleet Management", "Ride Hailing",
    "Smart City Projects", "Edge Computing", "5G Networks", "Telecom Billing",
    "Data Centre Operations", "Server Management", "Storage Engineering", "Disaster Recovery",
    "Business Continuity", "Containerization", "Kubernetes", "Microservices Architecture",
    "API Design", "GraphQL", "Event-driven Architecture", "Streaming Platforms",
    "Data Lakes", "Feature Stores", "Model Registry", "LLM Fine-tuning",
    "Prompt Engineering", "RAG Systems", "Vector Databases", "Semantic Search",
    "Knowledge Graphs", "Graph Databases", "Time Series Forecasting", "Monte Carlo Simulation",
    "Optimization Modelling", "Game Theory", "Crowdsourcing", "Hackathon Management",
    "Developer Relations", "Open Source Governance", "Compiler Design", "Formal Verification",
    "Fuzzing", "Bug Bounty", "Vulnerability Research", "Reverse Engineering",
    "Malware Analysis", "Threat Hunting", "OSINT", "Security Awareness",
    "Regulatory Technology", "Healthtech", "Telemedicine", "Wearable Health",
    "Mental Health Apps", "Clinical Trials Management", "Biostatistics", "Real World Evidence",
    "Surgical Robotics", "Imaging AI", "Radiology AI", "Genomic Medicine",
    "Personalized Medicine", "Vaccine Development", "Structural Biology", "Synthetic Biology",
    "Systems Biology", "Computational Chemistry", "Materials Informatics", "Fuel Cells",
    "Hydrogen Economy", "Carbon Capture", "Space Debris", "Satellite Propulsion",
    "Mission Planning", "Orbital Mechanics", "Cubesat Development", "Planetary Science",
    "Astrobiology", "Astrophysics", "Particle Physics", "Nuclear Fusion",
    "Plasma Physics", "Cryo-EM", "Nanoelectronics", "Graphene Research",
    "Quantum Materials", "Superconductors", "Spintronics", "Perovskite Solar Cells",
    "Organic Electronics", "Flexible Electronics", "Bioelectronics", "Brain-computer Interface",
    "Neuroprosthetics", "Assistive Technology", "Prosthetics Design", "Rehabilitation Robotics",
    "Humanoid Robotics", "Swarm Robotics", "Agricultural Robotics", "Medical Robotics",
    "Soft Robotics", "Sensors", "Microfluidics", "Point-of-care Diagnostics",
    "Biosensors", "Remote Patient Monitoring", "Elder Care Technology", "Smart Home",
    "Energy Management Systems", "Smart Metering", "Microgrids", "EV Charging Infrastructure",
    "Battery Swapping", "Vehicle-to-grid", "Autonomous Driving", "Vehicle Telematics",
    "Usage-based Insurance", "Road Safety", "Traffic Simulation", "Metro Operations",
    "Railway Signalling", "High-speed Rail", "Air Traffic Control", "Aircraft Maintenance",
    "Airport Security", "Border Control", "Visa Processing", "Trade Facilitation",
    "Shipping Operations", "Port Planning", "Crane Automation", "Harbour Engineering",
    "Maritime Safety", "Search and Rescue", "Missile Systems", "Aero-engines",
    "Propulsion", "Aerodynamics", "Composites", "Additive Manufacturing",
    "CNC Machining", "Metrology", "Non-destructive Testing", "Welding Technology",
    "Packaging Engineering", "Tissue Engineering", "Regenerative Medicine", "Stem Cell Research",
    "3D Bioprinting", "Biomaterials", "Implant Design", "Veterinary Diagnostics",
    "Poultry Science", "Food Processing", "Food Packaging", "Sensory Analysis",
    "Consumer Research", "Retail Analytics", "Shopper Insights", "Category Management",
    "Omnichannel", "Customer Loyalty", "Marketing Automation", "Email Marketing",
    "SEO", "Growth Hacking", "Influencer Marketing", "Public Relations",
    "Crisis Communication", "Employer Branding", "Talent Acquisition", "Talent Management",
    "Learning and Development", "Performance Management", "Payroll Operations", "Diversity and Inclusion",
    "Employee Engagement", "Executive Search", "Remote Work", "Facilities Management",
    "Green Procurement", "Supplier Diversity", "Ethical Trade", "Human Rights Due Diligence",
    "Fair Trade", "Food Safety Standards", "HACCP", "ISO 9001",
    "Six Sigma", "Lean Management", "Process Excellence", "Workflow Automation",
    "Low-code Development", "Digital Transformation", "Strategy Consulting", "Management Consulting",
    "Operations Consulting", "Financial Consulting", "HR Consulting", "IT Consulting",
    "Risk Consulting", "Economic Consulting", "Public Sector Consulting", "Social Impact Consulting",
    "Nonprofit Management", "NGO Operations", "Fundraising", "Grant Management",
    "Impact Assessment", "Social Audit", "CSR Strategy", "Volunteer Management",
    "Disaster Response", "Humanitarian Logistics", "Refugee Support", "Community Health",
    "Vaccination Drives", "Health Education", "Nutrition Programs", "Food Security",
    "Livelihood Programs", "Skill Development", "Entrepreneurship Training", "Incubation Management",
    "Startup Mentoring", "Venture Capital Research", "Fund Management", "Private Equity",
    "ESG Investing", "Impact Investing", "Sustainable Finance", "Green Bonds",
    "Climate Finance", "Public Finance", "Fiscal Policy", "Trade Policy",
    "Consumer Protection", "Data Protection", "Privacy Engineering", "Trust and Safety",
    "Content Moderation", "Platform Governance", "Algorithmic Fairness", "AI Ethics",
    "Responsible AI", "Explainable AI", "Adversarial ML", "Federated Learning",
    "Differential Privacy", "Homomorphic Encryption", "Post-quantum Cryptography", "Hardware Security",
    "Firmware Security", "Secure Boot", "Confidential Computing", "Zero Trust",
    "Microsegmentation", "SIEM", "SOAR", "Threat Modelling",
    "Attack Surface Management", "Exposure Management", "Vendor Risk", "Supply Chain Security",
    "Vulnerability Management", "Patch Management", "Identity and Access Management", "Secrets Management",
    "Key Management", "PKI", "OAuth", "SSO",
    "MFA", "Passwordless", "Biometric Authentication", "Behavioural Biometrics",
    "Fraud Scoring", "Risk-based Authentication", "Customer Identity", "Identity Verification",
    "AML Screening", "Sanctions Screening", "Financial Crime", "Account Takeover",
    "Bot Detection", "WAF", "DDoS Mitigation", "CDN",
    "Serverless", "Function-as-a-Service", "Service Mesh", "API Gateway",
    "Load Balancing", "Autoscaling", "Cost Optimization", "FinOps",
    "Cloud Migration", "Legacy Modernization", "Mainframe Modernization", "ERP Implementation",
    "CRM Implementation", "Master Data", "Reference Data", "Data Quality",
    "Data Profiling", "Entity Resolution", "Geocoding", "Location Intelligence",
    "Geofencing", "Indoor Positioning", "Computer Vision in Retail", "Smart Shelves",
    "Personalization", "Collaborative Filtering", "Reinforcement Learning", "Transfer Learning",
    "Self-supervised Learning", "Contrastive Learning", "Foundation Models", "Large Language Models",
    "Multimodal Models", "Speech Models", "TTS", "ASR",
    "Machine Translation", "Sentiment Analysis", "Toxic Language Detection", "Named Entity Recognition",
    "Question Answering", "Summarization", "Dialogue Systems", "Conversational AI",
    "Voice Bots", "Contact Center AI", "Knowledge Management", "Neural Search",
    "Learning to Rank", "CTR Modelling", "Conversion Prediction", "LTV Prediction",
    "Propensity Modelling", "Next Best Action", "Campaign Optimization", "Media Planning",
    "Incrementality", "MTA", "MMM", "Econometrics",
    "Causal Inference", "Experimental Design", "Bayesian Analysis", "Sequential Testing",
    "Multi-armed Bandits", "Online Experimentation", "Feature Flagging", "GitOps",
    "Infrastructure as Code", "Terraform", "Ansible", "Policy as Code",
    "Security Scanning", "SAST", "DAST", "Container Scanning",
    "CVE Research", "CVSS", "Remediation Tracking", "Compliance Reporting",
    "Audit Logs", "Memory Forensics", "Disk Forensics", "Network Forensics",
    "Incident Reconstruction", "Root Cause Analysis", "Reliability Engineering", "SRE",
    "Capacity Planning", "Performance Engineering", "Load Testing", "Chaos Engineering",
    "Fault Injection", "Resilience Testing", "Disaster Simulation", "Launch Reviews",
    "Data Migration", "Schema Migration", "Zero-downtime Migrations", "Cutover Planning",
    "Go-live Support", "Hypercare", "On-call", "SLA Management",
    "SLO", "Error Budgets", "Triage", "War Rooms",
    "Major Incident", "Crisis Management", "Business Impact Analysis", "Failover Testing",
    "Multi-region", "Multi-cloud", "Hybrid Cloud", "DNS",
    "BGP", "Subnetting", "SD-WAN", "MPLS",
    "VPN", "Network Segmentation", "Firewall Rules", "Reverse Proxy",
    "Caching", "Redis", "Message Broker", "Pub/Sub",
    "Event Sourcing", "CQRS", "Saga Pattern", "Distributed Transactions",
    "Consensus", "Raft", "Leader Election", "Distributed Locking",
    "Idempotency", "Retry Logic", "Circuit Breaker", "Dead Letter Queue",
    "Exactly-once", "Sharding", "Replication", "Change Data Capture",
    "Snapshot Isolation", "Query Optimization", "Columnar Storage", "Vectorized Execution",
    "Parquet", "Protobuf", "Feature Flags", "Service Discovery",
    "etcd", "ZooKeeper", "Vault", "Nomad",
    "Terraform Cloud", "Packer", "Vagrant",
]

ORGS = [
    "Tata Consultancy Services", "Infosys", "Wipro", "HCL Technologies", "Tech Mahindra",
    "LTIMindtree", "Cognizant", "Accenture", "Capgemini", "IBM India", "Microsoft India",
    "Google India", "Amazon India", "Flipkart", "Paytm", "PhonePe", "Razorpay",
    "Zoho Corporation", "Freshworks", "Zomato", "Swiggy", "Ola", "Rapido",
    "Larsen & Toubro", "Reliance Industries", "Tata Motors", "Mahindra & Mahindra",
    "Bharat Electronics", "Hindustan Aeronautics", "ISRO", "DRDO", "BHEL", "NTPC",
    "Adani Group", "JSW Steel", "Tata Steel", "HDFC Bank", "ICICI Bank", "State Bank of India",
    "Axis Bank", "Kotak Mahindra Bank", "Bajaj Finance", "NITI Aayog", "World Bank India",
    "UNICEF India", "UNDP India", "National Skill Development Corporation", "AICTE Internship Cell",
    "TULIP Portal Partner", "C-DAC", "Indian Army (Technical)", "Railway Board", "Power Grid Corporation",
    "GAIL India", "ONGC", "Indian Oil", "Bharti Airtel", "Reliance Jio", "Vodafone Idea",
    "Siemens India", "Bosch India", "ABB India", "Schneider Electric", "Honeywell India",
    "Cisco India", "Dell Technologies", "HP India", "Oracle India", "SAP Labs India",
    "Salesforce India", "Adobe India", "ServiceNow India", "PayPal India", "Stripe India",
    "Uber India", "Airbnb India", "LinkedIn India", "Meta India", "Twitter/X India",
    "Netflix India", "Spotify India", "BYJU'S", "Unacademy", "Vedantu", "upGrad",
    "Coursera India", "Practo", "PharmEasy", "Cure.fit", "Myntra", "Nykaa",
    "Meesho", "BigBasket", "Blinkit", "Dunzo", "Urban Company", "Porter",
    "Delhivery", "Blue Dart", "FedEx India", "MakeMyTrip", "IRCTC", "RedBus",
    "Oyo", "Treebo", "Ather Energy", "Ola Electric", "TVS Motor", "Hero MotoCorp",
    "Bajaj Auto", "Royal Enfield", "Ashok Leyland", "Maruti Suzuki", "Hyundai India",
    "Kia India", "Tata Power", "Adani Green Energy", "ReNew Power", "Suzlon", "Siemens Gamesa",
    "L&T Energy", "Vedanta", "Hindalco", "Ultratech Cement", "ACC Cement", "Lafarge India",
    "Godrej Industries", "ITC Limited", "Hindustan Unilever", "P&G India", "Nestle India",
    "Britannia", "Amul", "Mother Dairy", "Parle Products", "Dabur", "Marico",
    "Emami", "Bajaj Electricals", "Havells", "Crompton", "Voltas", "Blue Star",
    "Titan Company", "Tanishq", "Bata India", "Reliance Retail", "DMart", "Big Bazaar (Future)",
    "Trent (Westside)", "Shoppers Stop", "Decathlon India", "Adidas India", "Nike India",
    "Puma India", "Levi's India", "Zara India", "H&M India", "Fabindia", "Khadi India",
]

# program sources per AICTE internship ecosystem
PROGRAM_SOURCES = ["General", "TULIP", "CDAC", "Indian Army"]
PROGRAM_WEIGHTS = [0.80, 0.08, 0.07, 0.05]

MODES = ["offline", "online", "hybrid"]
MODE_WEIGHTS = [0.55, 0.25, 0.20]

DESC_TEMPLATES = [
    "Work on live projects with the {org} {domain} team; contribute to sprint deliverables and documentation.",
    "Assist senior engineers/researchers on {domain} tasks, from requirements to testing and delivery.",
    "Hands-on {domain} internship: build, test and ship a real feature under a mentor.",
    "Support data collection, analysis and reporting for the {domain} practice at {org}.",
    "Collaborate across teams on {domain} initiatives; present findings to stakeholders.",
    "Learn industry workflows in {domain} while supporting day-to-day operations and quality checks.",
]


def build_internships(seed: int = 20260816, count: int = TARGET_ROWS) -> list[dict]:
    rng = random.Random(seed + 6)
    fake = Faker("en_IN")
    fake.seed_instance(seed + 6)
    registry = json.loads((PROJECT_ROOT / "institute_registry.json").read_text(encoding="utf-8"))
    names = [i["name"] for i in registry["institutes"]]

    rows = []
    for _ in range(count):
        stipend = 0 if rng.random() < 0.30 else rng.randint(3_000, 15_000)
        rows.append({
            "institution_name": rng.choice(names),
            "domain": rng.choice(DOMAIN_AREAS),
            "organization_name": rng.choice(ORGS),
            "duration_weeks": rng.randint(6, 9),
            "stipend_amount": stipend,
            "mode": rng.choices(MODES, weights=MODE_WEIGHTS, k=1)[0],
            "is_ppo_linked": rng.random() < 0.15,
            "program_source": rng.choices(PROGRAM_SOURCES, weights=PROGRAM_WEIGHTS, k=1)[0],
            "description": rng.choice(DESC_TEMPLATES).format(
                org=fake.company(), domain="<domain>"),
        })
    # fix the placeholder with the actual domain after the fact
    for row in rows:
        row["description"] = row["description"].replace("<domain>", row["domain"].lower())
    return rows


def main() -> None:
    import pandas as pd

    rows = build_internships()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8")
    print("=" * 60)
    print(f"[internships] wrote {len(df)} rows -> {OUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"[internships] domains        : {df['domain'].nunique()} distinct")
    print(f"[internships] orgs           : {df['organization_name'].nunique()} distinct")
    print(f"[internships] unpaid share   : {(df['stipend_amount'] == 0).mean():.0%}")
    print(f"[internships] PPO linked     : {df['is_ppo_linked'].mean():.0%}")
    print(f"[internships] by source      : {df['program_source'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
