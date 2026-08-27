class HumanInterventionRequiredException(Exception):
    def __init__(self, message: str, intervention_type: str):
        super().__init__(message)
        self.message = message
        self.intervention_type = intervention_type  # "CAPTCHA", "OTP", "LOGIN_REQUIRED"
