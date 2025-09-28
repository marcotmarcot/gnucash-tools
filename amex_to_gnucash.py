import csv
import sys
import re
from datetime import datetime
from bs4 import BeautifulSoup, Comment
from decimal import Decimal, ROUND_HALF_UP

# --- Configuration ---
# You can change these values if your Gnucash accounts are different.
CREDIT_CARD_ACCOUNT = "Total:Passivo:Cartão de Crédito:Amex"
DEFAULT_EXPENSE_ACCOUNT = "Não classificado"

# --- Account Categorization Rules ---
# Keywords are case-insensitive.
ACCOUNT_RULES = {
    "Despesas:Cachorro": ["GROOMING"],
    "Despesas:Celular": ["SMARTY"],
    "Despesas:Diversão": ["YOUTUBEPREMIUM"],
    "Despesas:Educação": ["RHODES AVENUE", "BREEZY CLUB"],
    "Despesas:Saúde": ["BOOTS", "MASSAGE", "PHARMACY", "FUSSY", "SUNDAYS INSURANCE"],
    "Despesas:Supermercado": ["PACTCOFFEE", "WAITROSE", "SAINSBURY", "OCADO", "ASTRID BAKERY", "MORRISONS", "ASDA", "GAIL", "BRAZILIAN CENTRE"],
    "Despesas:Vestuário": ["UNIQLO", "HAIR-TRIBE"],
    "Total:Ativo:Conta corrente:Monzo": ["PAYMENT RECEIVED"],
}
# --- End of Configuration ---

def parse_year_from_comment(soup):
    """
    Finds the 'saved from url' HTML comment and extracts the year from the URL.
    This is a reliable way to get the statement year.
    """
    try:
        comments = soup.find_all(string=lambda text: isinstance(text, Comment))
        for comment in comments:
            if 'saved from url' in comment:
                match = re.search(r'end=(\d{4})-\d{2}-\d{2}', comment)
                if match:
                    return match.group(1)
    except Exception as e:
        print(f"Warning: Could not automatically determine the year. Error: {e}")
    
    print("Warning: Could not find the statement year. Using the current year as a fallback.")
    return str(datetime.now().year)

def parse_deliveroo_orders(deliveroo_html_path):
    """
    Parses the Deliveroo orders HTML file and returns a dictionary for easy lookup.
    """
    deliveroo_orders = {}
    try:
        with open(deliveroo_html_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
    except FileNotFoundError:
        print(f"Warning: Deliveroo orders file '{deliveroo_html_path}' not found. Deliveroo transactions will not be detailed.")
        return deliveroo_orders
    except Exception as e:
        print(f"Warning: Could not read Deliveroo orders file. Error: {e}")
        return deliveroo_orders

    order_list_items = soup.find_all('li', class_='OrderList-550fb988658cc6b5')
    
    for item in order_list_items:
        try:
            restaurant_name = item.find('p', class_='ccl-2d0aeb0c9725ce8b').text.strip()
            # The last <p> tag contains both amount and date
            details_text = item.find_all('p', class_='ccl-2d0aeb0c9725ce8b')[-1].text
            
            # Extract amount and date, e.g., "£ 85.93 • 13 July 2025"
            amount_str, date_str = [part.strip() for part in details_text.split('•')]
            
            # Clean and convert amount to a Decimal for precision
            amount = Decimal(amount_str.replace('£', '').replace('€', '').replace('\xa0', '').strip())
            
            # Parse date
            date_obj = datetime.strptime(date_str, '%d %B %Y').date()

            # Create a lookup key (date, amount)
            lookup_key = (date_obj, amount)
            if lookup_key not in deliveroo_orders:
                 deliveroo_orders[lookup_key] = []
            deliveroo_orders[lookup_key].append(restaurant_name)

        except (AttributeError, ValueError, IndexError) as e:
            # This might happen if an order item has a different structure (e.g., canceled)
            print(f"Skipping a Deliveroo order item due to parsing error: {e}")
            continue
            
    print(f"Found {sum(len(v) for v in deliveroo_orders.values())} Deliveroo orders.")
    return deliveroo_orders

def get_account_and_description(description, date_obj, amount_value, deliveroo_orders):
    """
    Determines the correct expense account and description based on the rules.
    """
    account = DEFAULT_EXPENSE_ACCOUNT
    
    # Special handling for Deliveroo first as it's more specific
    if "DELIVEROO" in description.upper():
        # Use two decimal places for matching amounts
        amount_decimal = Decimal(str(amount_value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        lookup_key = (date_obj.date(), amount_decimal)
        
        if lookup_key in deliveroo_orders and deliveroo_orders[lookup_key]:
            # Pop the first available restaurant for this key to handle multiple identical orders on the same day
            restaurant_name = deliveroo_orders[lookup_key].pop(0)
            description = f"Deliveroo: {restaurant_name}"
            account = "Despesas:Comida"
        else:
            # Fallback if no match is found
            account = "Despesas:Comida"
            
    # Apply other rules
    for rules_account, keywords in ACCOUNT_RULES.items():
        if any(keyword in description.upper() for keyword in keywords):
            return description, rules_account

    return description, account

def process_html_file(amex_path, deliveroo_path, output_path):
    """
    Reads the Amex HTML, processes transactions with Deliveroo data and categorization, and writes to CSV.
    """
    try:
        with open(amex_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
    except FileNotFoundError:
        print(f"Error: The Amex file '{amex_path}' was not found.")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred while reading the Amex file: {e}")
        sys.exit(1)

    year = parse_year_from_comment(soup)
    if not year:
        sys.exit(1)

    # Parse Deliveroo orders first to create the lookup table
    deliveroo_orders = parse_deliveroo_orders(deliveroo_path)

    transaction_table_body = soup.find('tbody', {'data-testid': 'axp-activity-feed-transactions-table-body'})
    if not transaction_table_body:
        print("Error: Could not find the transaction table in the Amex HTML file.")
        sys.exit(1)
    
    transactions = transaction_table_body.find_all('tr', {'data-testid': re.compile(r'^transaction-row-')})

    if not transactions:
        print("No transactions found in Amex file.")
        return

    print(f"Found {len(transactions)} Amex transactions for the year {year}. Processing...")

    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Date", "Description", "Account", "Amount Debit", "Amount Credit", "Transfer Account"])

        for trx in reversed(transactions):
            columns = trx.find_all('td')
            if len(columns) < 5:
                continue

            try:
                # Check for pending status in the second column. If "Pending" is found, skip this transaction.
                status_text = columns[1].text.strip()
                if 'pending' in status_text.lower():
                    # Attempt to get a description for the log message, but don't fail if it's not there.
                    try:
                        pending_desc = columns[2].find('a').text.strip()
                        print(f"Skipping pending transaction: {pending_desc}")
                    except AttributeError:
                        print("Skipping a pending transaction (description not available).")
                    continue
                
                date_str = columns[0].find('div', class_='font-weight-regular').text.strip()
                full_date_str = f"{date_str} {year}"
                date_obj = datetime.strptime(full_date_str, '%d %b %Y')
                formatted_date = date_obj.strftime('%Y-%m-%d')

                original_description = columns[2].find('a').text.strip()
                
                # Check for supplementary cardholder info (e.g., the 'TO' badge)
                supp_card_badge = trx.find('span', class_=re.compile(r'_badge_'))
                supp_card_initials = ""
                if supp_card_badge:
                    initials = supp_card_badge.text.strip()
                    if initials:
                        supp_card_initials = f" ({initials})"

                amount_text = columns[4].find('p').text.strip()
                amount_value = float(amount_text.replace('£', '').replace(',', '').replace(' ', ''))
                
                # Get the base description and account from our rules
                description, expense_account = get_account_and_description(
                    original_description, date_obj, abs(amount_value), deliveroo_orders
                )
                
                # Append the supplementary card initials to the final description
                final_description = f"{description}{supp_card_initials}"
                
                debit_amount, credit_amount = ("", abs(amount_value)) if amount_value < 0 else (amount_value, "")
                
                writer.writerow([formatted_date, final_description, expense_account, debit_amount, credit_amount, CREDIT_CARD_ACCOUNT])

            except (AttributeError, ValueError) as e:
                print(f"Skipping a row due to a parsing error: {e}")
                continue
    
    print(f"\nSuccessfully converted the statement to '{output_path}'.")
    print("You can now import this file into Gnucash.")

def main():
    """
    Main function to handle script execution.
    """
    if len(sys.argv) != 3:
        print("Usage: python amex_to_gnucash.py <path_to_amex_statement.html> <path_to_deliveroo_orders.html>")
        sys.exit(1)
        
    amex_file = sys.argv[1]
    deliveroo_file = sys.argv[2]
    output_file = "amex_gnucash_categorized.csv"
    
    process_html_file(amex_file, deliveroo_file, output_file)

if __name__ == "__main__":
    main()
