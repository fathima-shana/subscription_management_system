"""Password policy required by the brief:
more than eight characters, with an uppercase letter, a lowercase letter and a special character.
"""
from django.core.exceptions import ValidationError

MIN_LENGTH = 9  # "more than eight"


class StrongPasswordValidator:
    def validate(self, password, user=None):
        errors = []
        if len(password) < MIN_LENGTH:
            errors.append(
                ValidationError("Use more than 8 characters.", code="password_too_short")
            )
        if not any(c.isupper() for c in password):
            errors.append(
                ValidationError("Include an uppercase letter.", code="password_no_upper")
            )
        if not any(c.islower() for c in password):
            errors.append(
                ValidationError("Include a lowercase letter.", code="password_no_lower")
            )
        if not any(not c.isalnum() and not c.isspace() for c in password):
            errors.append(
                ValidationError(
                    "Include a special character (for example ! @ # $ %).",
                    code="password_no_special",
                )
            )
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return (
            "More than 8 characters, with an uppercase letter, "
            "a lowercase letter and a special character."
        )
