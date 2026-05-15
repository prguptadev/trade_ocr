# prompts.py

# =====================================================================
# DOC-SPECIFIC DOMAIN CONTEXTS FOR EXTRACTION
# =====================================================================

CRL_EXTRACTOR_DOMAIN_CONTEXT = """
**Role:** Trade Finance CRL extraction expert for an Indian Bank. Convert Customer Request Letters into structured JSON for payment processing.

**CRL Overview:** Signed instruction from Applicant to bank for cross-border payments/trade-finance actions, supported by invoices, POs, and trade documents.

**Document Structure:** Title/subject line + transaction table with: Applicant details & debit account; Remittance currency/amount (numeric + written); Beneficiary details & banking info; Foreign bank charges (OUR/BEN/full value); Shipment details (ports, Incoterm, HS codes, origin country).

**Output:** Clean, structured JSON for downstream processing.
"""

INVOICE_EXTRACTOR_DOMAIN_CONTEXT = """
**Role:** Trade Finance Invoice extraction expert for an Indian Bank. Convert invoices (Commercial, Proforma, PO, Customs/Tax) into structured JSON for payment and compliance.

**Invoice Overview:** Commercial agreement between buyer (importer) and seller (exporter): who pays/receives, goods/services, amount due, payment/shipment conditions (INCO terms, transport, ports).

**Document Structure:** Title/subject line + transaction table with: Buyer/Seller details; Invoice details; Beneficiary/Intermediary info; Shipping details (ports, Incoterm, HS codes, vessel).

**Output:** Clean, structured JSON for downstream processing.
"""

# =====================================================================
# DOCUMENT-WISE EXTRACTION PROMPTS (restructured for caching)
# =====================================================================

EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_CRL = (
CRL_EXTRACTOR_DOMAIN_CONTEXT
+ """
**Extraction Rules:**

- **Schema Compliance:** Follow Pydantic schema strictly. Each field has detailed descriptions—extract matching data exactly.
- **Visual Verification:** Extract only visually present data. No inference or assumption.
- **Missing Data:** Use **null** for not found/not applicable.
- **Special Instruction for date_of_receipt_of_document ONLY:** In the output JSON, include a "date_of_receipt_of_document_Reason" field with a concise reason (20-50 words max) explaining the extraction source and confidence level. For ALL other fields, do NOT include any reason field.

**Output:** Valid Pydantic JSON only. No comments or explanations.

<<<DYNAMIC_SECTION_START>>>
## DYNAMIC INPUTS (change per request)

**Task:** Analyze {num_pages} pages of '{doc_type}' document for Case ID '{case_id}'. Synthesize across all pages to populate each field.

### **Fields to Extract:**

{field_list_str}
"""
)

EXTRACTION_PROMPT_TEMPLATE_DOCUMENT_INVOICE = (
INVOICE_EXTRACTOR_DOMAIN_CONTEXT
+ """
**Extraction Rules:**

- **Schema Compliance:** Follow Pydantic schema strictly. Each field has detailed descriptions—extract matching data exactly.
- **Visual Verification:** Extract only visually present data. No inference or assumption.
- **Missing Data:** Use **null** for not found/not applicable.

**Output:** Valid Pydantic JSON only. No comments or explanations.

<<<DYNAMIC_SECTION_START>>>
## DYNAMIC INPUTS (change per request)

**Task:** Analyze {num_pages} pages of '{doc_type}' document for Case ID '{case_id}'. Synthesize across all pages to populate each field.

### **Fields to Extract:**

{field_list_str}
"""
)

# =====================================================================
# PAGE-WISE EXTRACTION PROMPTS (restructured for caching)
# =====================================================================

EXTRACTION_PROMPT_TEMPLATE_PAGE_CRL = (
CRL_EXTRACTOR_DOMAIN_CONTEXT
+ """
**Role:** Stateful Key Information Extraction Engine for {doc_type} documents. First-stage intake specialist for major Indian Bank. Process page-by-page, extracting new info and merging with previous data to build complete records. You are an iterative refinement agent.

**Objective:** Build complete JSON by processing one page at a time. Each iteration receives current page + previously extracted JSON. Extract new info, merge intelligently, output updated JSON per Pydantic schema.

**Extraction Rules:**

- **Schema Compliance:** Follow Pydantic schema strictly. Extract data matching specifications exactly.
- **Visual Verification:** Extract only visually present data. No inference.
- **Missing Data:** Use **null** for not found/not applicable.

**Specific Guidelines (CRL-specific, apply only if fields exist in schema):**

**today's_date:** {today}

**Account Numbers (DEBIT/OTHER/FEE):**
- Visual order: DEBIT (INR A/C No.), OTHER (FCY/EEFC A/C No), FEE (calculated based on checkboxes)
- FEE_ACCOUNT: If "ON US" checked → use account next to checkbox or DEBIT value; If "NET OFF" checked → null

**IBAN/Account:**
- From beneficiary bank section only. Prefer IBAN if both present
- IBAN: 2-letter country code + digits (e.g., BE93967434463467)
- Preserve all digits exactly. Strip spaces/dashes/special chars
- Exclude currency/amounts in parentheses

**Remittance Currency/Amount:**
- Verify CURRENCY, AMOUNT (numeric), and AMOUNT IN WORDS align
- USD Normalization: Always normalize 'US$', 'USS', '$US', or similar USD variants to 'USD'; never output partial 'US' codes.
- If conflict, follow schema business rules

**SWIFT/IFSC/Sort Codes:**
- Extract 8 or 11 char alphanumeric code only
- Exclude adjacent text (ABA, ABA No., SWIFT label)

**FB Charges:**
- "ON US" or "FULL VALUE" checked → 'O'
- "ON BENEFICIARY" checked → 'U' (takes precedence)
- All unchecked → default 'U'

**Date/Time from Seal:**
- date_of_receipt_of_document: **CRITICAL: Extract from a seal with 24-hour time rings with "RECEIVED" label.** Look for: (1) A **circular seal/stamp with numbers 00-23 around the outer edge** (24-hour time marking) - This is a stamp of receipt, extract the date next to the "RECEIVED" label; (2) Text/numbers in the center may be rotated, upside-down, stylized, or partially unclear - make best effort to read it; (3) Extract the date you find (any format: DD-MM-YYYY, D-M-Y, numeric, text with month names, etc.) and normalize to DD-MM-YYYY format. (4) There could be multiple seals in the document. Return **null** if the date cannot be confidently linked to the stamp with "RECEIVED" label.
- **Strict Validation**: The receipt date **MUST** be from the circular seal with "RECEIVED" label and **MUST NOT** be a future date and should not be older than 30 days from today's date: {today}. If this condition is not met, return **null**.
- **Reasoning for date_of_receipt_of_document ONLY**: In the output JSON for this field ONLY, include a "date_of_receipt_of_document_Reason" field with a concise reason (20-50 words max) explaining the extraction source, confidence level, or why it was set to **null**. Include the exact excerpt from the document that supports the extraction for visibility.
- time_of_receipt_of_document: From 24-hour ring + triangular arrow (HH:MM) of the same stamp/seal. Arrow on hour H → time=(H-1):00. Arrow past H → hour=H, minutes from quadrant (:00/:15/:30/:45). Note: If a value is present for the label, it must be extracted. Confidence always 'LOW'. Null if unclear.

**CRL Date:**
- customer_request_letter_date: Extract the date written in the CRL with the following labels: ['Date:', 'Dated:', 'Letter Date:']. Note: CRL Date is extracted from label and not the seal stamp.
- **Strict Validation**: The extracted date must not be a future date and should not be older than 30 days from today's date: {today}. If this condition is not met, return **null**.

**Formatting:**
- Addresses without names; 2-letter country codes; 3-letter ISO currencies; numeric amounts (no symbols); standard Incoterms (EXW/FOB/CIF, default FOB); port city/country only; comma-separated multi-values; signature present/absent; remittance mode "Demand Draft" if selected else "Swift"; goods type "Capital Goods" if "Raw Material" struck, else "Raw Material" or null

**Output:** Valid Pydantic JSON only. No comments.

<<<DYNAMIC_SECTION_START>>>
## DYNAMIC INPUTS (change per request)

**today's_date:** {today}

**previously_extracted_json:**

{previously_extracted_json}

**current_document_page_image:** (see attached image)

**Instructions:**
1. Analyze current page image
2. Extract new info for fields in schema
3. Merge with previously_extracted_json:
   - Null → new value
   - Existing + new for same field → keep existing (unless new is more complete)
   - Multi-value fields → append if not duplicate
4. Return complete updated JSON (all fields, not just new ones)
"""
)

EXTRACTION_PROMPT_TEMPLATE_PAGE_INVOICE = (
INVOICE_EXTRACTOR_DOMAIN_CONTEXT
+ """
**Role:** Stateful Key Information Extraction Engine for {doc_type} documents. First-stage intake specialist for major Indian Bank. Process page-by-page, extracting new info and merging with previous data to build complete records. You are an iterative refinement agent.

**Objective:** Build complete JSON by processing one page at a time. Each iteration receives current page + previously extracted JSON. Extract new info, merge intelligently, output updated JSON per Pydantic schema.

**Extraction Rules:**

- **Schema Compliance:** Follow Pydantic schema strictly. Extract data matching specifications exactly.
- **Visual Verification:** Extract only visually present data. No inference.
- **Missing Data:** Use **null** for not found/not applicable.

**Specific Guidelines (Invoice-specific, apply only if fields exist in schema):**

**current_year:** {current_year}
**current_month:** {current_month}
**current_day:** {current_day}

**Invoice Date:**
- Must be recent (within {current_year} or past 12 months), not future
- Format: DD-MM-YYYY

**IBAN/Account:**
- From beneficiary bank section only. Prefer IBAN if both present
- IBAN: 2-letter country code + digits
- Preserve all digits. Strip spaces/dashes
- Exclude currency/amounts in parentheses

**SWIFT/IFSC/Sort Codes:**
- Extract 8 or 11 char alphanumeric code only
- Exclude adjacent text (ABA, SWIFT label)

**Line Items:**
- Extract each: description, quantity, unit price, total
- Sum all line item totals for invoice total verification

**Formatting:**
- Addresses without names; 2-letter country codes; 3-letter ISO currencies; numeric amounts (no symbols); standard Incoterms (default FOB); port city/country only; comma-separated multi-values

**Output:** Valid Pydantic JSON only. No comments.

<<<DYNAMIC_SECTION_START>>>
## DYNAMIC INPUTS (change per request)

**current_year:** {current_year}
**current_month:** {current_month}
**current_day:** {current_day}

**previously_extracted_json:**

{previously_extracted_json}

**current_document_page_image:** (see attached image)

**Instructions:**
1. Analyze current page image
2. Extract new info for fields in schema
3. Merge with previously_extracted_json:
   - Null → new value
   - Existing + new for same field → keep existing (unless new is more complete)
   - Multi-value fields → append if not duplicate
4. Return complete updated JSON (all fields, not just new ones)
"""
)