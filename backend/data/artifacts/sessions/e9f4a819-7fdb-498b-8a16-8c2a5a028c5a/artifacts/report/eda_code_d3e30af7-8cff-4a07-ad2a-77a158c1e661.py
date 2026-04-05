import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Load data
data = pd.read_parquet('data.parquet')

# Create stripplot for 'MperR' column
plt.figure(figsize=(10, 6))
sns.stripplot(data=data, y='MperR')
plt.title('Distribution of MperR')
plt.ylabel('MperR')
plt.grid(True)
plt.savefig('plot_1.png')
plt.close()