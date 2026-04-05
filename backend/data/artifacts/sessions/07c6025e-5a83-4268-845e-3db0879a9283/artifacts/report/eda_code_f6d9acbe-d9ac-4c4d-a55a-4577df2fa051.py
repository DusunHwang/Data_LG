import pandas as pd

# Load the data
data = pd.read_parquet('data.parquet')

# Calculate the mean of the MEDV column
mean_medv = data['MEDV'].mean()

# Save the result
with open('result_0.json', 'w') as f:
    json.dump({"mean_MEDV": mean_medv}, f)