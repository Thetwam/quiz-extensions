# -*- coding: utf-8 -*-

from __future__ import unicode_literals, print_function
from collections import defaultdict

from models import Course, Extension, User
from views import app
from report_config import BLACKLIST, INCLUDE_100, TERMS

largest_course = {
    'size': 0,
    'course': None
}

largest_user = {
    'size': 0,
    'user': None
}

with app.app_context():

    course_freqs = defaultdict(int)

    courses = Course.query.all()

    # Only get courses with names that match the query terms
    # TODO: see if this can be done faster in the query
    # TODO: add term ("semester term") to Course model to make searching easier.
    course_list = [c for c in courses if c.canvas_term_id in TERMS]

    print("Breakdown by course:\n")
    # print("Matching query terms: {}".format(", ".join(TERMS)))
    print("Number of Courses: {}".format(len(course_list)))
    for course in course_list:
        print("  - {} ({})".format(course.course_name, course.canvas_id))
        num_quizzes = course.quizzes.count()
        print("    {} quiz{}".format(num_quizzes, "zes" if num_quizzes != 1 else ""))

        if INCLUDE_100:
            num_extensions = course.extensions.count()
        else:
            num_extensions = course.extensions.filter(Extension.percent != 100).count()

        if num_extensions == 0:
            continue

        print("    {} extension{}:".format(num_extensions, "s" if num_extensions != 1 else ""))

        # Increment course frequency list for this particular count of extensions.
        course_freqs[int(num_extensions)] += 1

        if num_extensions > largest_course['size']:
            largest_course = {
                'size': num_extensions,
                'course': course
            }

        for extension in course.extensions:
            user = extension.user
            print("      - {}% {}".format(
                extension.percent,
                user.sortable_name
            ))

    # breakdown by users
    user_freqs = defaultdict(int)

    users = User.query.all()
    user_list = [u for u in users if u.canvas_id not in BLACKLIST]

    for user in user_list:
        if INCLUDE_100:
            num_extensions = user.extensions.count()
        else:
            num_extensions = user.extensions.filter(Extension.percent != 100).count()

        if num_extensions == 0:
            continue

        # Increment user frequency list for this particular count of extensions.
        user_freqs[int(num_extensions)] += 1

        if num_extensions > largest_user['size']:
            largest_user = {
                'size': num_extensions,
                'user': user
            }


print("\n------------------\n")
print("Summary:\n")

print("Course extensions frequency distribution:")
print("Num Ext | Num Courses")
print("------- | ----------")
for k, v in course_freqs.items():
    print("{}| {}".format(str(k).ljust(8), v))

print("Course with the most extensions:")
print("{} with {} extensions".format(
    largest_course['course'].course_name,
    largest_course['size']
))

print("\n")

print("User extensions frequency distribution:")
print("Num Ext | Num Users")
print("------- | ---------")
for k, v in user_freqs.items():
    print("{}| {}".format(str(k).ljust(8), v))

print("User with the most extensions:")
print("{} with {} extensions".format(
    largest_user['user'].sortable_name,
    largest_user['size']
))

# TODO list:
# - course(s) w/ highest number of extensions w/ count
# - frequency distribtion of extensions by student
# - student(s) w/ highest number of extensions w/ count (maybe, FERPA concerns)
# - total number of extensions
# - total number of students affected
# - total number of quizzes affected
