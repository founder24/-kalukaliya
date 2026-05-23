"""
Load Testing with Locust for Syrabit Backend

Run: locust -f apps/backend/tests/locustfile.py --host http://localhost:8000
Web UI: http://localhost:8089
"""
from locust import HttpUser, task, between, tag
import random


class AnonymousUser(HttpUser):
    """Simulates anonymous/free-tier chat users"""
    wait_time = between(2, 8)
    weight = 7

    test_messages = [
        "What is photosynthesis?",
        "Explain Newton's laws of motion",
        "What is the capital of Assam?",
        "How does DNA replication work?",
        "Explain the water cycle",
    ]

    @task(8)
    @tag("chat")
    def chat_message(self):
        """Send a chat message"""
        self.client.post("/api/v1/chat/", json={
            "message": random.choice(self.test_messages),
            "lang": random.choice(["en", "as"]),
        })

    @task(2)
    @tag("chat")
    def chat_stream(self):
        """Send a streaming chat request"""
        with self.client.post("/api/v1/chat/stream", json={
            "message": random.choice(self.test_messages),
        }, stream=True, catch_response=True) as response:
            if response.status_code == 200:
                for _ in response.iter_lines():
                    pass
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    @task(1)
    @tag("health")
    def health_check(self):
        """Hit health endpoint"""
        self.client.get("/health")


class AuthenticatedUser(HttpUser):
    """Simulates authenticated Pro-tier users"""
    wait_time = between(1, 5)
    weight = 3

    def on_start(self):
        """Login on start"""
        response = self.client.post("/api/v1/auth/login", json={
            "email": f"loadtest_{self.environment.runner.user_count}@test.com",
            "password": "loadtest_password_123",
        })
        if response.status_code == 200:
            data = response.json()
            self.token = data.get("access_token", "")
            self.headers = {"Authorization": f"Bearer {self.token}"}
        else:
            self.token = ""
            self.headers = {}

    @task(6)
    @tag("chat")
    def authenticated_chat(self):
        """Chat as authenticated user"""
        self.client.post("/api/v1/chat/", json={
            "message": "Explain quantum entanglement in simple terms",
        }, headers=self.headers)

    @task(2)
    @tag("profile")
    def get_profile(self):
        """Fetch user profile"""
        self.client.get("/api/v1/users/me", headers=self.headers)

    @task(1)
    @tag("subscription")
    def check_subscription(self):
        """Check subscription status"""
        self.client.get("/api/v1/subscription/status", headers=self.headers)

    @task(1)
    @tag("history")
    def get_chat_history(self):
        """Fetch chat history with pagination"""
        self.client.get("/api/v1/chat/history?skip=0&limit=10", headers=self.headers)
