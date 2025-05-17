CREATE TABLE IF NOT EXISTS "badges" ("Id" INT, "UserId" INT, "Name" VARCHAR, "Date" TIMESTAMP, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "comments" ("Id" INT, "PostId" INT, "Score" INT, "Text" VARCHAR, "CreationDate" TIMESTAMP, "UserId" INT, "UserDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "postHistory" ("Id" INT, "PostHistoryTypeId" INT, "PostId" INT, "RevisionGUID" VARCHAR, "CreationDate" TIMESTAMP, "UserId" INT, "Text" VARCHAR, "Comment" VARCHAR, "UserDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "postLinks" ("Id" INT, "CreationDate" TIMESTAMP, "PostId" INT, "RelatedPostId" INT, "LinkTypeId" INT, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "posts" ("Id" INT, "PostTypeId" INT, "AcceptedAnswerId" INT, "CreaionDate" TIMESTAMP, "Score" INT, "ViewCount" INT, "Body" VARCHAR, "OwnerUserId" INT, "LasActivityDate" TIMESTAMP, "Title" VARCHAR, "Tags" VARCHAR, "AnswerCount" INT, "CommentCount" INT, "FavoriteCount" INT, "LastEditorUserId" INT, "LastEditDate" TIMESTAMP, "CommunityOwnedDate" TIMESTAMP, "ParentId" INT, "ClosedDate" TIMESTAMP, "OwnerDisplayName" VARCHAR, "LastEditorDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "tags" ("Id" INT, "TagName" VARCHAR, "Count" INT, "ExcerptPostId" INT, "WikiPostId" INT, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "users" ("Id" INT, "Reputation" INT, "CreationDate" TIMESTAMP, "DisplayName" VARCHAR, "LastAccessDate" TIMESTAMP, "WebsiteUrl" VARCHAR, "Location" VARCHAR, "AboutMe" VARCHAR, "Views" INT, "UpVotes" INT, "DownVotes" INT, "AccountId" INT, "Age" INT, "ProfileImageUrl" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "votes" ("Id" INT, "PostId" INT, "VoteTypeId" INT, "CreationDate" DATE, "UserId" INT, "BountyAmount" INT, PRIMARY KEY ("Id"));

SELECT T3.Tags FROM users AS T1 INNER JOIN postHistory AS T2 ON T1.Id = T2.UserId INNER JOIN posts AS T3 ON T3.Id = T2.PostId WHERE T1.DisplayName = 'Mark Meckes' AND T3.CommentCount = 0;

SELECT T2.TagName FROM users AS T1 INNER JOIN posts AS T3 ON T1.Id = T3.OwnerUserId INNER JOIN tags AS T2 ON T3.Id = T2.ExcerptPostId LEFT JOIN comments AS T4 ON T3.Id = T4.PostId WHERE T1.DisplayName = 'Mark Meckes' AND T4.Id IS NULL