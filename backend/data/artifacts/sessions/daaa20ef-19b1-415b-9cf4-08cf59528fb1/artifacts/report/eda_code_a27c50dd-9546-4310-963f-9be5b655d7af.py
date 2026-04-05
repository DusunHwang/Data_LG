import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Load data
data = pd.read_parquet('data.parquet')

# Set up the matplotlib figure
fig, axes = plt.subplots(nrows=5, ncols=3, figsize=(20, 30))
axes = axes.flatten()

# Plot stripplot and boxplot for each numeric column
for i, col in enumerate(data.select_dtypes(include=['float64']).columns):
    sns.stripplot(x=data[col], ax=axes[i], size=2, alpha=0.6)
    sns.boxplot(x=data[col], ax=axes[i], width=0.2, color='red', fliersize=0)
    axes[i].set_title(f'{col} Distribution')
    axes[i].set_xlabel('Value')

# Hide empty subplots
for j in range(i + 1, len(axes)):
    axes[j].set_visible(False)

plt.tight_layout()
plt.savefig('plot_1.png')
plt.close()