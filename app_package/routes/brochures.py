import os
import uuid
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app, send_from_directory)
from app_package import db
from app_package.models import Brochure

brochures_bp = Blueprint('brochures', __name__)


def allowed_brochure(filename):
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in current_app.config['ALLOWED_BROCHURE_EXTENSIONS']


def validate_mime(file_stream):
    try:
        import magic
        mime = magic.from_buffer(file_stream.read(2048), mime=True)
        file_stream.seek(0)
        return mime in current_app.config['ALLOWED_BROCHURE_MIMETYPES']
    except ImportError:
        # python-magic not available, skip MIME check
        return True


@brochures_bp.route('/')
def list_brochures():
    brochures = db.session.query(Brochure).order_by(Brochure.created_at.desc()).all()
    return render_template('brochures/list.html', brochures=brochures)


@brochures_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not file.filename:
            flash('Please select a file.', 'error')
            return redirect(url_for('brochures.upload'))

        if not allowed_brochure(file.filename):
            flash('Only PDF, PNG, and JPG files are allowed.', 'error')
            return redirect(url_for('brochures.upload'))

        if not validate_mime(file):
            flash('File MIME type is not allowed.', 'error')
            return redirect(url_for('brochures.upload'))

        ext = file.filename.rsplit('.', 1)[1].lower()
        stored_name = f"{uuid.uuid4().hex}.{ext}"
        save_path = os.path.join(current_app.config['BROCHURE_FOLDER'], stored_name)
        file.save(save_path)

        file_size = os.path.getsize(save_path)

        is_default = request.form.get('is_default') == 'on'
        if is_default:
            db.session.query(Brochure).update({Brochure.is_default: False})

        brochure = Brochure(
            original_filename=file.filename,
            stored_filename=stored_name,
            file_type=ext,
            file_size=file_size,
            is_default=is_default,
            description=request.form.get('description', '').strip()
        )
        db.session.add(brochure)
        db.session.commit()

        flash('Brochure uploaded successfully!', 'success')
        return redirect(url_for('brochures.list_brochures'))

    return render_template('brochures/upload.html')


@brochures_bp.route('/set-default/<int:brochure_id>', methods=['POST'])
def set_default(brochure_id):
    db.session.query(Brochure).update({Brochure.is_default: False})
    brochure = db.get_or_404(Brochure, brochure_id)
    brochure.is_default = True
    db.session.commit()
    flash(f'"{brochure.original_filename}" set as default brochure.', 'success')
    return redirect(url_for('brochures.list_brochures'))


@brochures_bp.route('/delete/<int:brochure_id>', methods=['POST'])
def delete(brochure_id):
    brochure = db.get_or_404(Brochure, brochure_id)
    filepath = os.path.join(current_app.config['BROCHURE_FOLDER'], brochure.stored_filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(brochure)
    db.session.commit()
    flash('Brochure deleted.', 'success')
    return redirect(url_for('brochures.list_brochures'))


@brochures_bp.route('/file/<filename>')
def serve_file(filename):
    return send_from_directory(current_app.config['BROCHURE_FOLDER'], filename)
