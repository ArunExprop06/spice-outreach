import os
import uuid
import csv
import io
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, current_app, send_file)
from app_package import db
from app_package.models import Contact, MessageLog
import pandas as pd

contacts_bp = Blueprint('contacts', __name__)

CONTACT_CATEGORIES = [
    'Manufacturer', 'Exporter', 'Trader', 'Wholesaler',
    'Retailer', 'Distributor', 'Supplier', 'Service Provider', 'Other'
]
CONTACT_STATUSES = ['new', 'contacted', 'responded', 'converted', 'inactive']


@contacts_bp.route('/')
def list_contacts():
    contacts = db.session.query(Contact).order_by(Contact.created_at.desc()).all()
    return render_template('contacts/list.html', contacts=contacts,
                           categories=CONTACT_CATEGORIES, statuses=CONTACT_STATUSES)


@contacts_bp.route('/add', methods=['GET', 'POST'])
def add_contact():
    if request.method == 'POST':
        contact = Contact(
            company_name=request.form.get('company_name', '').strip(),
            contact_person=request.form.get('contact_person', '').strip(),
            email=request.form.get('email', '').strip(),
            phone=request.form.get('phone', '').strip(),
            whatsapp=request.form.get('whatsapp', '').strip(),
            website=request.form.get('website', '').strip(),
            city=request.form.get('city', '').strip(),
            state=request.form.get('state', '').strip(),
            country=request.form.get('country', 'India').strip(),
            category=request.form.get('category', 'Other'),
            notes=request.form.get('notes', '').strip(),
            source='manual'
        )
        db.session.add(contact)
        db.session.commit()
        flash('Contact added successfully!', 'success')
        return redirect(url_for('contacts.list_contacts'))
    return render_template('contacts/form.html', contact=None,
                           categories=CONTACT_CATEGORIES, statuses=CONTACT_STATUSES)


@contacts_bp.route('/edit/<int:contact_id>', methods=['GET', 'POST'])
def edit_contact(contact_id):
    contact = db.get_or_404(Contact, contact_id)
    if request.method == 'POST':
        contact.company_name = request.form.get('company_name', '').strip()
        contact.contact_person = request.form.get('contact_person', '').strip()
        contact.email = request.form.get('email', '').strip()
        contact.phone = request.form.get('phone', '').strip()
        contact.whatsapp = request.form.get('whatsapp', '').strip()
        contact.website = request.form.get('website', '').strip()
        contact.city = request.form.get('city', '').strip()
        contact.state = request.form.get('state', '').strip()
        contact.country = request.form.get('country', 'India').strip()
        contact.category = request.form.get('category', 'Other')
        contact.status = request.form.get('status', 'new')
        contact.notes = request.form.get('notes', '').strip()
        db.session.commit()
        flash('Contact updated successfully!', 'success')
        return redirect(url_for('contacts.list_contacts'))
    return render_template('contacts/form.html', contact=contact,
                           categories=CONTACT_CATEGORIES, statuses=CONTACT_STATUSES)


@contacts_bp.route('/delete/<int:contact_id>', methods=['POST'])
def delete_contact(contact_id):
    contact = db.get_or_404(Contact, contact_id)
    db.session.delete(contact)
    db.session.commit()
    flash('Contact deleted.', 'success')
    return redirect(url_for('contacts.list_contacts'))


@contacts_bp.route('/detail/<int:contact_id>')
def detail(contact_id):
    contact = db.get_or_404(Contact, contact_id)
    messages = db.session.query(MessageLog).filter_by(contact_id=contact_id).order_by(MessageLog.created_at.desc()).all()
    return render_template('contacts/detail.html', contact=contact, messages=messages)


@contacts_bp.route('/export')
def export_csv():
    contacts = db.session.query(Contact).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Company Name', 'Contact Person', 'Email', 'Phone', 'WhatsApp',
                     'Website', 'City', 'State', 'Country', 'Category', 'Status', 'Notes'])
    for c in contacts:
        writer.writerow([c.company_name, c.contact_person, c.email, c.phone, c.whatsapp,
                         c.website, c.city, c.state, c.country, c.category, c.status, c.notes])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='contacts_export.csv'
    )


@contacts_bp.route('/import', methods=['GET', 'POST'])
def import_csv():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not file.filename:
            flash('Please select a file.', 'error')
            return redirect(url_for('contacts.import_csv'))

        ext = file.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('csv', 'xlsx', 'xls'):
            flash('Only CSV and Excel files are supported.', 'error')
            return redirect(url_for('contacts.import_csv'))

        # Save temp file
        temp_name = f"{uuid.uuid4().hex}.{ext}"
        temp_path = os.path.join(current_app.config['CSV_TEMP_FOLDER'], temp_name)
        file.save(temp_path)

        # Read columns
        try:
            if ext == 'csv':
                df = pd.read_csv(temp_path, nrows=5)
            else:
                df = pd.read_excel(temp_path, nrows=5)
            columns = df.columns.tolist()
            preview = df.head(5).to_dict('records')
        except Exception as e:
            flash(f'Error reading file: {e}', 'error')
            os.remove(temp_path)
            return redirect(url_for('contacts.import_csv'))

        db_fields = ['company_name', 'contact_person', 'email', 'phone', 'whatsapp',
                     'website', 'city', 'state', 'country', 'category', 'notes']

        return render_template('contacts/import_map.html',
                               columns=columns, preview=preview,
                               db_fields=db_fields, temp_file=temp_name)

    return render_template('contacts/import.html')


@contacts_bp.route('/import/process', methods=['POST'])
def import_process():
    temp_file = request.form.get('temp_file')
    if not temp_file:
        flash('No file to process.', 'error')
        return redirect(url_for('contacts.import_csv'))

    temp_path = os.path.join(current_app.config['CSV_TEMP_FOLDER'], temp_file)
    if not os.path.exists(temp_path):
        flash('Temp file expired. Please upload again.', 'error')
        return redirect(url_for('contacts.import_csv'))

    ext = temp_file.rsplit('.', 1)[-1].lower()
    try:
        if ext == 'csv':
            df = pd.read_csv(temp_path)
        else:
            df = pd.read_excel(temp_path)
    except Exception as e:
        flash(f'Error reading file: {e}', 'error')
        return redirect(url_for('contacts.import_csv'))

    # Build column mapping from form
    mapping = {}
    for key in request.form:
        if key.startswith('map_') and request.form[key]:
            db_field = key[4:]
            csv_col = request.form[key]
            if csv_col in df.columns:
                mapping[db_field] = csv_col

    if 'company_name' not in mapping:
        flash('Company Name mapping is required.', 'error')
        return redirect(url_for('contacts.import_csv'))

    count = 0
    duplicates = 0
    for _, row in df.iterrows():
        company = str(row.get(mapping['company_name'], '')).strip()
        if not company or company == 'nan':
            continue

        email = str(row.get(mapping.get('email', ''), '')).strip()
        if email == 'nan':
            email = ''

        # Duplicate check by company name + email
        existing = db.session.query(Contact).filter_by(company_name=company).first()
        if existing:
            duplicates += 1
            continue

        contact = Contact(
            company_name=company,
            contact_person=str(row.get(mapping.get('contact_person', ''), '')).strip().replace('nan', ''),
            email=email,
            phone=str(row.get(mapping.get('phone', ''), '')).strip().replace('nan', ''),
            whatsapp=str(row.get(mapping.get('whatsapp', ''), '')).strip().replace('nan', ''),
            website=str(row.get(mapping.get('website', ''), '')).strip().replace('nan', ''),
            city=str(row.get(mapping.get('city', ''), '')).strip().replace('nan', ''),
            state=str(row.get(mapping.get('state', ''), '')).strip().replace('nan', ''),
            country=str(row.get(mapping.get('country', ''), 'India')).strip().replace('nan', '') or 'India',
            category=str(row.get(mapping.get('category', ''), 'Other')).strip().replace('nan', '') or 'Other',
            notes=str(row.get(mapping.get('notes', ''), '')).strip().replace('nan', ''),
            source='csv'
        )
        db.session.add(contact)
        count += 1

    db.session.commit()

    # Clean up temp file
    try:
        os.remove(temp_path)
    except OSError:
        pass

    flash(f'Imported {count} contacts. {duplicates} duplicates skipped.', 'success')
    return redirect(url_for('contacts.list_contacts'))
