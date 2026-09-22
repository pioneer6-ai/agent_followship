# Test Patient Data Files

This folder contains sample patient list files in various formats to test the upload and LLM parsing functionality.

## 📁 Files Included:

### 1. **hospital_patients_format1.csv** 
Standard English format with clean headers
- 8 patients
- Standard date format: YYYY-MM-DD
- Common field names

### 2. **dental_clinic_patients.csv**
Chinese format (测试中文支持)
- 5 patients
- Shows multi-language support
- Chinese headers and content

### 3. **patient_list_irregular.csv**
Messy format with inconsistent naming
- 6 patients
- Mixed date formats (MM/DD/YYYY, DD-MM-YYYY, YYYY-MM-DD)
- Inconsistent field names (mobile vs phone, mail_address vs email)
- Tests LLM's ability to handle variations

### 4. **patients.json**
JSON format with nested structure
- 4 patients
- Nested contact information
- Different field naming conventions
- Tests JSON parsing capabilities

### 5. **patient_notes.txt**
Natural language format (hardest to parse)
- 5 patients
- Free-form text with patient information
- Inconsistent formatting
- Tests LLM's natural language understanding

## 🧪 How to Test:

1. Start the Flask application:
   ```bash
   python app.py
   ```

2. Open http://localhost:8080 in your browser

3. Click the **📤 Upload Patient List** button

4. Upload any of these test files

5. Watch the AI parse the data automatically

6. Review the parsed patients

7. Click **Import All Patients** to add them to the system

8. Check **Active Follow-up Cases** to see the new patients

## ✅ Expected Results:

All files should be parsed successfully, with the AI:
- Recognizing different field names
- Converting various date formats
- Mapping treatment descriptions to standard types
- Calculating days overdue
- Handling missing data gracefully

## 📝 Notes:

- The irregular format file is specifically designed to test the parser's robustness
- The Chinese format file tests multi-language support
- The text file tests natural language parsing (most challenging)
- All test patients have realistic data based on dental clinic scenarios
