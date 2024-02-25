import pygame
import random

# Initialize Pygame
pygame.init()

# Set the window dimensions
window_width = 600
window_height = 400

# Set up the display with borderless windowed mode
screen = pygame.display.set_mode((window_width, window_height), pygame.NOFRAME)

# Colors and settings
background_color = (30, 30, 30)
positive_token_color = (0, 255, 0)
negative_token_color = (255, 0, 0)
ball_color = (255, 255, 0)
paddle_color = (255, 255, 255)
ball_width = 10
ball_height = 10
ball_speed_x = 7
ball_speed_y = 7
font_size = 24
game_font = pygame.font.Font(None, font_size)
score_left = 0
score_right = 0
clock = pygame.time.Clock()

# Token class
class Token:
    def __init__(self, positive=True):
        self.x = random.randint(20, window_width - 20)
        self.y = random.randint(20, window_height - 20)
        self.positive = positive
        self.color = positive_token_color if positive else negative_token_color
        self.rect = pygame.Rect(self.x, self.y, 10, 10)

    def reset(self):
        self.x = random.randint(20, window_width - 20)
        self.y = random.randint(20, window_height - 20)
        self.rect = pygame.Rect(self.x, self.y, 10, 10)

positive_token = Token(positive=True)
negative_token = Token(positive=False)

# Paddle class
class TextPaddle:
    def __init__(self, x, y, text, facing_right=True):
        self.x = x
        self.y = y
        self.text = text
        self.speed = 6
        self.skill_level = 95
        self.text_surface = game_font.render(text, True, paddle_color)
        if facing_right:
            self.text_surface = pygame.transform.rotate(self.text_surface, 90)
        else:
            self.text_surface = pygame.transform.rotate(self.text_surface, -90)
        self.rect = self.text_surface.get_rect(center=(x, y))

    def move(self, direction):
        if direction == "up":
            self.y -= self.speed
        elif direction == "down":
            self.y += self.speed
        self.y = max(self.rect.height // 2, min(window_height - self.rect.height // 2, self.y))
        self.rect = self.text_surface.get_rect(center=(self.x, self.y))

    def is_hit(self, ball):
        return self.rect.colliderect(ball.rect)

# Ball class with spin functionality and reset method adjustment
class Ball:
    def __init__(self):
        self.reset()

    def move(self):
        self.x += self.speed_x
        self.y += self.speed_y
        if self.y <= 0 or self.y >= window_height - ball_height:
            self.speed_y *= -1
        self.rect = pygame.Rect(self.x, self.y, ball_width, ball_height)

    def reset(self):
        self.x = window_width // 2
        self.y = window_height // 2
        self.speed_x = ball_speed_x * random.choice([-1, 1])
        self.speed_y = ball_speed_y * random.choice([-1, 1])
        self.rect = pygame.Rect(self.x, self.y, ball_width, ball_height)

    def spin(self, paddle):
        difference_in_y = paddle.y - self.y
        normalized_difference = difference_in_y / (paddle.rect.height / 2)
        max_spin_effect = 5
        self.speed_y -= normalized_difference * max_spin_effect

left_paddle = TextPaddle(30, window_height // 2, "pythagoratheorem", True)
right_paddle = TextPaddle(window_width - 30, window_height // 2, "zirk", False)
ball = Ball()

# Display scores
def display_scores():
    score_text = f"{left_paddle.text}: {score_left}  {right_paddle.text}: {score_right}"
    score_surface = game_font.render(score_text, True, paddle_color)
    screen.blit(score_surface, (window_width // 2 - score_surface.get_width() // 2, 10))

# AI control function
def ai_control(paddle, ball):
    decision = random.randint(0, 100)
    if decision < paddle.skill_level:  # Making AI miss only ~5% of the time
        if ball.y > paddle.y + paddle.rect.height / 2:
            paddle.move("down")
        elif ball.y < paddle.y - paddle.rect.height / 2:
            paddle.move("up")

# Token collision and scoring logic
def handle_token_collision():
    global score_left, score_right
    if ball.rect.colliderect(positive_token.rect):
        if ball.speed_x > 0:  # Right paddle hit it last
            score_right += 1
        else:
            score_left += 1
        positive_token.reset()
    elif ball.rect.colliderect(negative_token.rect):
        if ball.speed_x > 0:  # Right paddle hit it last
            score_right = max(0, score_right - 1)  # Ensure score doesn't go negative
        else:
            score_left = max(0, score_left - 1)  # Ensure score doesn't go negative
        negative_token.reset()

# Checking for scoring
def check_score():
    global score_left, score_right
    if ball.x <= 0:  # Ball has gone past the left edge, right player scores
        score_right += 1
        ball.reset()  # Reset the ball for the next round
    elif ball.x >= window_width:  # Ball has gone past the right edge, left player scores
        score_left += 1
        ball.reset()  # Reset the ball for the next round

# Main game loop
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    ai_control(left_paddle, ball)
    ai_control(right_paddle, ball)

    ball.move()
    handle_token_collision()
    check_score()

    if left_paddle.is_hit(ball) or right_paddle.is_hit(ball):
        ball.speed_x *= -1
        if left_paddle.is_hit(ball):
            ball.spin(left_paddle)
        else:
            ball.spin(right_paddle)

    screen.fill(background_color)
    screen.blit(left_paddle.text_surface, left_paddle.rect.topleft)
    screen.blit(right_paddle.text_surface, right_paddle.rect.topleft)
    pygame.draw.ellipse(screen, ball_color, ball.rect)
    pygame.draw.rect(screen, positive_token.color, positive_token.rect)
    pygame.draw.rect(screen, negative_token.color, negative_token.rect)
    pygame.draw.aaline(screen, paddle_color, (window_width // 2, 0), (window_width // 2, window_height))

    display_scores()

    pygame.display.flip()
    clock.tick(60)

pygame.quit()
