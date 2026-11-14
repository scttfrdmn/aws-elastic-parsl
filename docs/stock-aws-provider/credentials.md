# Credentials

`AWSProvider` creates one boto3 session in its constructor and uses it for everything: building the
infrastructure, launching and polling instances, and teardown. The workers never receive AWS
credentials. If your apps need AWS access on the workers, give the instances a role with
`iam_instance_profile_arn`.

## How the provider finds credentials

### With the fixes (pending upstream PRs, [#2](https://github.com/scttfrdmn/aws-elastic-parsl/issues/2), [#3](https://github.com/scttfrdmn/aws-elastic-parsl/issues/3))

The first of these that is set wins:

1. **`key_file=`.** A JSON file containing `AWSAccessKeyId` and `AWSSecretKey`, and optionally
   `AWSSessionToken`. The boto3 names (`aws_access_key_id`, `aws_secret_access_key`,
   `aws_session_token`) are also accepted. Any other key raises `ConfigurationError`. The path may use
   `~` and environment variables.
2. **`profile=`.** A named profile from `~/.aws/config` or `~/.aws/credentials`. A profile that does
   not exist raises `ConfigurationError` instead of botocore's `ProfileNotFound`.
3. **boto3's default credential chain.** This covers, in order:
   - the `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` variables;
   - the profile named by `AWS_PROFILE` (or `[default]`), including SSO / IAM Identity Center,
     `credential_process` and `role_arn` profiles;
   - web identity;
   - container credentials (ECS, EKS Pod Identity);
   - the EC2 instance role.

   If the chain finds nothing, the constructor raises `ConfigurationError`. This check runs before
   anything is created.

So on a laptop, `AWS_PROFILE=myprofile python run.py` works without `profile=`. On EC2, an instance
role works with no configuration.

### Parsl ≤ 2026.11.09 (released)

- **Accepted credentials.** The constructor raises `ConfigurationError` unless `profile=` or
  `key_file=` is set, or both `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are in the environment.
  `AWS_PROFILE` on its own, `[default]` and instance roles are all rejected, even though boto3 would
  find them.
- **`key_file`.** The documented `AWSAccessKeyId`/`AWSSecretKey` keys raise
  `TypeError: Session.__init__() got an unexpected keyword argument 'AWSAccessKeyId'`. Only the boto3
  names work.
- **`profile`.** A misspelled `profile=` raises botocore's `ProfileNotFound`.

The workarounds:

| Where you run | Released workaround |
|---|---|
| Laptop with `AWS_PROFILE` (including SSO) | `profile=os.environ['AWS_PROFILE']` |
| Laptop with static keys | Export `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (and `AWS_SESSION_TOKEN`), or use a `key_file` with the boto3 key names |
| EC2 instance role, ECS or EKS | Add a `[default]` section to `~/.aws/config` (a `region =` line is enough) and pass `profile='default'` |

The last workaround works because, when a profile is named explicitly, boto3 skips the environment
variables but still tries the container and instance-metadata providers. A profile with no keys of its
own therefore resolves to the role.

SSO sessions expire. If a long run fails part way with `ExpiredToken` or
`UnauthorizedSSOTokenError`, refresh with `aws sso login` and start again. The running instances and
the state file are unaffected.

## Region

The session's region decides where everything is created, and `image_id` must be an AMI in that region.

- **Parsl ≤ 2026.11.09 (released).** `region` defaults to `'us-east-2'`, and that value is always passed
  to boto3. It overrides `AWS_DEFAULT_REGION` and the profile's `region`. A run without `region=`
  creates its VPC in us-east-2, wherever you meant it to go.
- **With the fixes ([#11](https://github.com/scttfrdmn/aws-elastic-parsl/issues/11)).** `region`
  defaults to `None`, which lets boto3 resolve the region from `AWS_DEFAULT_REGION`, then from the
  `region` of the selected profile in `~/.aws/config`. If neither is set, the provider falls back to
  `us-east-2` and logs this at INFO. After construction, `provider.region` holds the region actually
  used.

**`AWS_REGION` is not read.** boto3/botocore read only `AWS_DEFAULT_REGION`, unlike the AWS CLI v2 and
some other SDKs, which also read `AWS_REGION`. If you set only `AWS_REGION`, the provider ignores it.
Set `AWS_DEFAULT_REGION`, a profile `region`, or `region=`.

Pass `region=` explicitly in configs that must work on both versions.

## IAM permissions

The provider calls these EC2 APIs. The examples in this guide also look up the AMI in SSM, which needs
`ssm:GetParameter`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ParslAWSProviderInfrastructure",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateVpc",
        "ec2:DeleteVpc",
        "ec2:CreateInternetGateway",
        "ec2:AttachInternetGateway",
        "ec2:DetachInternetGateway",
        "ec2:DeleteInternetGateway",
        "ec2:DescribeInternetGateways",
        "ec2:CreateRouteTable",
        "ec2:CreateRoute",
        "ec2:AssociateRouteTable",
        "ec2:DeleteRouteTable",
        "ec2:DescribeRouteTables",
        "ec2:DescribeAvailabilityZones",
        "ec2:CreateSubnet",
        "ec2:ModifySubnetAttribute",
        "ec2:DeleteSubnet",
        "ec2:DescribeSubnets",
        "ec2:CreateSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:AuthorizeSecurityGroupEgress",
        "ec2:DeleteSecurityGroup",
        "ec2:DescribeSecurityGroups",
        "ec2:CreateTags",
        "ec2:DescribeVpcs"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ParslAWSProviderInstances",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceTypeOfferings"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AmiLookup",
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:*::parameter/aws/service/*"
    }
  ]
}
```

Notes:

- **Instance roles for workers.** If you set `iam_instance_profile_arn`, the caller also needs
  `iam:PassRole` on that role.
- **Spot.** The first Spot request in an account may need `iam:CreateServiceLinkedRole`, to create
  `AWSServiceRoleForEC2Spot`.
- **Infrastructure permissions.** The infrastructure actions are needed only when the provider creates
  or tears down its VPC. A submit host that only runs workflows against existing infrastructure needs
  the instance actions plus `ec2:DescribeSubnets` and `ec2:CreateTags`. Tags are applied at launch
  through `TagSpecifications`, which requires `ec2:CreateTags`.
- **Narrowing the policy.** The policy uses `"Resource": "*"` for simplicity. With the fixes, every
  resource the provider creates carries the tag `parsl:provider=AWSProvider`, so you can restrict
  deletes with an `aws:ResourceTag/parsl:provider` condition. With the fixes, teardown describes each
  resource to check its tags before deleting it, so keep the `Describe*` actions.

## Credentials that the workers need

To use AWS from your apps (S3 and so on), create an IAM role and instance profile for the workers. Then
pass the instance profile's ARN, not the role's:

```python
provider = AWSProvider(
    image_id='ami-0123456789abcdef0',
    key_name='my-key',
    region='us-west-2',
    iam_instance_profile_arn='arn:aws:iam::123456789012:instance-profile/parsl-worker',
)
```

Apps on the worker then get credentials from instance metadata through boto3's default chain.

- **Released.** `IamInstanceProfile={'Arn': ''}` is sent even when no profile is set. EC2 accepts it.
- **With the fixes ([#9](https://github.com/scttfrdmn/aws-elastic-parsl/issues/9)).** It is omitted.

Do not put long-lived keys in `worker_init`. User data is readable by anyone on the instance through
instance metadata, and by anyone with `ec2:DescribeInstanceAttribute` in your account.
