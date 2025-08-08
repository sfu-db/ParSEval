{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "INTEGER"
        }
      ],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "INTEGER"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "OR",
            "operator": "OR",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "LESS_THAN_OR_EQUAL",
                "operator": "<=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 7,
                    "name": "$7",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 6.0,
                    "type": "DECIMAL",
                    "nullable": false,
                    "precision": 2
                  }
                ]
              },
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
                        "index": 7,
                        "name": "$7",
                        "type": "FLOAT"
                      },
                      {
                        "kind": "LITERAL",
                        "value": 8.5,
                        "type": "DECIMAL",
                        "nullable": false,
                        "precision": 2
                      }
                    ]
                  },
                  {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "OTHER_FUNCTION",
                        "operator": "STRFTIME",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "LITERAL",
                            "value": "%Y",
                            "type": "CHAR",
                            "nullable": false,
                            "precision": 2
                          },
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "VARCHAR",
                            "operands": [
                              {
                                "kind": "CAST",
                                "operator": "CAST",
                                "type": "TIMESTAMP",
                                "operands": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 1,
                                    "name": "$1",
                                    "type": "DATE"
                                  }
                                ]
                              }
                            ]
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "1997",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
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
              "table": "Laboratory",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}