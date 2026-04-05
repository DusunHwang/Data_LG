import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Load data
df = pd.read_parquet('data.parquet')

# Calculate correlation matrix
corr = df.corr()

# Find top 3 numeric columns with highest correlation with 'MperR'
top_corr_columns = corr['MperR'].sort_values(ascending=False).index[1:4].tolist()

# Plot scatter plots for MperR vs each of the top 3 columns
for col in top_corr_columns:
    plt.figure(figsize=(8, 6))
    sns.scatterplot(x='MperR', y=col, data=df)
    plt.title(f'Scatter plot of MperR vs {col}')
    plt.xlabel('MperR')
    plt.ylabel(col)
    plt.savefig(f'plot_1.png')
    plt.close()

# Plot histograms for the top 3 columns
for col in top_corr_columns:
    plt.figure(figsize=(8, 6))
    sns.histplot(df[col], kde=True)
    plt.title(f'Distribution of {col}')
    plt.xlabel(col)
    plt.ylabel('Frequency')
    plt.savefig(f'plot_2.png')
    plt.close()