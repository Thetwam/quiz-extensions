# -*- coding: utf-8 -*-

from __future__ import unicode_literals, print_function

from canvasapi import Canvas
from canvasapi.exceptions import ResourceDoesNotExist

from config import API_URL, API_KEY
from models import Course
from views import app, db

canvas = Canvas(API_URL, API_KEY)

with app.app_context():

    courses = Course.query.all()

    for course in courses:
        course_id = course.canvas_id
        term = None
        try:
            canvas_course = canvas.get_course(course_id)
            term = canvas_course.enrollment_term_id
            print('Course #{} is term {}'.format(course_id, term))
        except ResourceDoesNotExist:
            print('Course #{} not found. Term is null'.format(course_id))

        course.canvas_term_id = term
        db.session.commit()
