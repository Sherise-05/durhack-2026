def recommend_nap(tiredness, goal):
    if tiredness > 7:
        duration = 90  # Full sleep cycle
    elif goal == 'refresh':
        duration = 20  # Power nap
    else:
        duration = 45  # Default nap
    return {
        'duration': duration,
        'reason': f"Based on your tiredness level ({tiredness}) and goal ({goal})."
    }