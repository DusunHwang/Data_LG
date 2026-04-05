import pandas as pd
import matplotlib.pyplot as plt

# Load the data
data = pd.read_parquet('data.parquet')

# Create boxplot for MEDV column
plt.figure(figsize=(8, 6))
data['MEDV'].plot(kind='box', vert=False, title='MEDV Distribution')
plt.xlabel('MEDV Values')
plt.grid(True)
plt.savefig('plot_1.png')
plt.close()