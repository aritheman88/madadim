
'''

cbsFixer.py
Input Xlsx produced from the cbs website and output cleaner table.

'''


import pandas as pd

def transform_cbs_excel_with_split_headers(input_file, output_file):
    # Load the Excel file
    xls = pd.ExcelFile(input_file)
    df_raw = pd.read_excel(xls, sheet_name=xls.sheet_names[0], header=None)

    # Find header row dynamically (first row where col 0 == 'פריט')
    header_row = df_raw[df_raw.iloc[:, 0] == 'פריט'].index[0]
    metadata = df_raw.iloc[:header_row]
    data = df_raw.iloc[header_row:].reset_index(drop=True)
    data.columns = data.iloc[0]
    data = data.iloc[1:].reset_index(drop=True)

    # Reshape from wide to long format
    data_long = data.melt(id_vars=['פריט', 'שנה'], var_name='חודש', value_name='ערך')

    # Convert Hebrew months to MM format
    month_map = {
        'ינואר': '01', 'פברואר': '02', 'מרס': '03', 'אפריל': '04', 'מאי': '05', 'יוני': '06',
        'יולי': '07', 'אוגוסט': '08', 'ספטמבר': '09', 'אוקטובר': '10', 'נובמבר': '11', 'דצמבר': '12'
    }
    data_long['חודש'] = data_long['חודש'].map(month_map)
    data_long = data_long.dropna(subset=['חודש', 'שנה'])

    # Create date column
    data_long['תאריך'] = data_long['שנה'].astype(str) + '-' + data_long['חודש']

    # Split 'פריט' into code and label
    data_long[['קוד', 'קטגוריה']] = data_long['פריט'].str.extract(r'(\d+)\s*-\s*(.+)')

    # Pivot with two rows per column: label, then code
    pivot = data_long.pivot(index='תאריך', columns='קטגוריה', values='ערך')
    code_map = data_long.drop_duplicates(subset='קטגוריה').set_index('קטגוריה')['קוד']

    # Build new header with two rows: first = category name, second = code
    new_header = pd.MultiIndex.from_tuples([
        (cat, code_map.get(cat, '')) for cat in pivot.columns
    ])
    pivot.columns = new_header
    pivot = pivot.sort_index().reset_index()

    # Save to Excel (with index=True to allow MultiIndex columns)
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        pivot.to_excel(writer, sheet_name='reshaped_data', index=True)
        metadata.to_excel(writer, sheet_name='metadata', header=False, index=False)

# Example usage:
transform_cbs_excel_with_split_headers(
    r"C:\Users\Ariel\PycharmProjects\MyPythonScripts\cbs\file_b4f5f42e-4206-4c35-a0be-6b17129f6a9b.xlsx",
    r"C:\Users\Ariel\PycharmProjects\MyPythonScripts\cbs\output.xlsx"
)
