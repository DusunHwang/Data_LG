import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Load the data
data = pd.read_parquet('data.parquet')

# Create a strip plot of MEDV by CAT.MEDV
plt.figure(figsize=(10, 6))
sns.stripplot(x='CAT.MEDV', y='MEDV', data=data, jitter=True, alpha=0.6)
plt.title('Distribution of MEDV by CAT.MEDV')
plt.xlabel('CAT.MEDV')
plt.ylabel('MEDV')
plt.grid(True)
plt.savefig('plot_1.png')
plt.close()