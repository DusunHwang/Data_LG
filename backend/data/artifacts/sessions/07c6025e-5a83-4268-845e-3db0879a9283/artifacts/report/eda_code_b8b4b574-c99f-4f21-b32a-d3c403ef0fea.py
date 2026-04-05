import pandas as pd
import matplotlib.pyplot as plt

# Load the data
data = pd.read_parquet('data.parquet')

# Create boxplot for MEDV column
plt.figure(figsize=(8, 6))
data['MEDV'].plot(kind='box', title='MEDV Boxplot')
plt.ylabel('MEDV')
plt.grid(True)
plt.savefig('plot_1.png')
plt.close()