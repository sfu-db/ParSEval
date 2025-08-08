{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "REAL",
      "operands": [
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "REAL",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
            }
          ]
        },
        {
          "kind": "CASE",
          "operator": "CASE",
          "type": "INTEGER",
          "operands": [
            {
              "kind": "EQUALS",
              "operator": "=",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "LITERAL",
                  "value": 10,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": "NULL",
              "type": "INTEGER",
              "nullable": true,
              "precision": 10
            },
            {
              "kind": "LITERAL",
              "value": 10,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        }
      ]
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": null
        }
      ],
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "INTEGER",
              "operands": [
                {
                  "kind": "AND",
                  "operator": "AND",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "GREATER_THAN_OR_EQUAL",
                      "operator": ">=",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 1,
                          "name": "$1",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "LITERAL",
                          "value": 2000,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        }
                      ]
                    },
                    {
                      "kind": "LESS_THAN_OR_EQUAL",
                      "operator": "<=",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 1,
                          "name": "$1",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "LITERAL",
                          "value": 2010,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        }
                      ]
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": 1,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            }
          ],
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "AND",
                "operator": "AND",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "GREATER_THAN_OR_EQUAL",
                    "operator": ">=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 5,
                        "name": "$5",
                        "type": "DATE"
                      },
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "DATE",
                        "operands": [
                          {
                            "kind": "LITERAL",
                            "value": "2000-01-01",
                            "type": "CHAR",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "LESS_THAN_OR_EQUAL",
                    "operator": "<=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 5,
                        "name": "$5",
                        "type": "DATE"
                      },
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "DATE",
                        "operands": [
                          {
                            "kind": "LITERAL",
                            "value": "2010-12-31",
                            "type": "CHAR",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
              "id": "1",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "races",
                  "id": "0",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}