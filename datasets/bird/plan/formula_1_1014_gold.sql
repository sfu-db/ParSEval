{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "MIN",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "VARCHAR"
        }
      ],
      "type": "VARCHAR",
      "name": "lap_record"
    }
  ],
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
        }
      ],
      "id": "8",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 6,
                "name": "$6",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Austrian Grand Prix",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          "variableset": "[]",
          "id": "7",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 5,
                    "name": "$5",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 10,
                    "name": "$10",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "6",
              "inputs": [
                {
                  "relOp": "LogicalJoin",
                  "joinType": "inner",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 0,
                        "name": "$0",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 2,
                        "name": "$2",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "4",
                  "inputs": [
                    {
                      "relOp": "LogicalProject",
                      "project": [
                        {
                          "kind": "INPUT_REF",
                          "index": 1,
                          "name": "$1",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "INPUT_REF",
                          "index": 15,
                          "name": "$15",
                          "type": "VARCHAR"
                        }
                      ],
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalFilter",
                          "condition": {
                            "kind": "NOT",
                            "operator": "NOT",
                            "type": "BOOLEAN",
                            "operands": [
                              {
                                "kind": "IS_NULL",
                                "operator": "IS NULL",
                                "type": "BOOLEAN",
                                "operands": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 15,
                                    "name": "$15",
                                    "type": "VARCHAR"
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
                              "table": "results",
                              "id": "0",
                              "inputs": []
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "races",
                      "id": "3",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "circuits",
                  "id": "5",
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